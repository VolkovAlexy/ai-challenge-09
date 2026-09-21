"""Хранение сессий в SQLite: автосохранение без риска потери при закрытии/сбое.

`SessionStore` — один файл (по умолчанию `sessions/sessions.db`). Две таблицы:
`sessions` (имя, системный промпт, саммари, его длина, настройки) и `messages`
(история по seq). Настройки и сообщения хранятся как JSON (pydantic `model_dump_json`),
чтобы схемы оставались forward-compatible. `snapshot` — атомарный upsert в
одной транзакции (полная замена истории), поэтому torn-write невозможен.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent.config.schema import AgentSettings
from agent.core.message import Message
from agent.memory.branching import DEFAULT_BRANCH, BranchState
from agent.memory.session import SessionData, save_session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at);
"""

# миграции со старых схем (IF NOT EXISTS/добавление колонок — idempotent)
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN summary TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN compacted_upto INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE sessions ADD COLUMN facts TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE sessions ADD COLUMN active_branch TEXT NOT NULL DEFAULT 'main'",
    "ALTER TABLE sessions ADD COLUMN branches_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE sessions ADD COLUMN scratchpad TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT ''",
]


@dataclass
class SessionInfo:
    """Строка в палитре /session: метаданные для вывода (без истории)."""

    id: str
    title: str
    updated_at: str
    model: str | None = None
    message_count: int | None = None


def _now() -> str:
    # timespec=microseconds — фиксированная длина, чтобы ISO-строки корректно
    # сортировались лексикографически (ORDER BY updated_at).
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _shorten(text: str, limit: int = 56) -> str:
    """Тема сессии: первая строка без переводов строк, обрезка с «…»."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


class SessionStore:
    """SQLite-хранилище сессий: автосохранение, список, загрузка, экспорт."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        for statement in _MIGRATIONS:
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute(statement)  # колонка уже есть — миграция не нужна
        self._conn.commit()

    @staticmethod
    def new_id() -> str:
        return uuid4().hex

    def snapshot(
        self,
        session_id: str,
        name: str,
        settings: AgentSettings,
        system_prompt: str,
        history: list[Message],
        summary: str | None = None,
        compacted_upto: int = 0,
        facts: dict[str, str] | None = None,
        scratchpad: str = "",
        active_branch: str = DEFAULT_BRANCH,
        branches: dict[str, BranchState] | None = None,
    ) -> None:
        """Атомарный upsert-снапшот: метаданные + полная замена истории.

        `history` — история активной ветки; `branches` — неактивные ветки
        (целиком, со своей историей), сериализуются в branches_json.
        """
        now = _now()
        settings_json = settings.model_dump_json()
        rows = [(session_id, seq, message.model_dump_json()) for seq, message in enumerate(history)]
        inactive = [
            {
                "name": branch_name,
                "summary": state.summary,
                "compacted_upto": state.compacted_upto,
                "facts": state.facts,
                "history": [message.model_dump() for message in state.history],
            }
            for branch_name, state in (branches or {}).items()
            if branch_name != active_branch
        ]
        with self._lock, self._conn:
            self._conn.execute(
                """
                    INSERT INTO sessions
                        (id, name, title, system_prompt, settings_json, summary, compacted_upto,
                         facts, scratchpad, active_branch, branches_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        system_prompt = excluded.system_prompt,
                        settings_json = excluded.settings_json,
                        summary = excluded.summary,
                        compacted_upto = excluded.compacted_upto,
                        facts = excluded.facts,
                        scratchpad = excluded.scratchpad,
                        active_branch = excluded.active_branch,
                        branches_json = excluded.branches_json,
                        updated_at = excluded.updated_at
                    """,
                (
                    session_id,
                    name,
                    "",
                    system_prompt,
                    settings_json,
                    summary or "",
                    compacted_upto,
                    json.dumps(facts or {}, ensure_ascii=False),
                    scratchpad,
                    active_branch,
                    json.dumps(inactive, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.executemany(
                "INSERT INTO messages (session_id, seq, message_json) VALUES (?, ?, ?)",
                rows,
            )

    def list(self, limit: int | None = None, offset: int = 0) -> list[SessionInfo]:
        """Непустые сессии, newest-first (по updated_at).

        Пустые (0 сообщений, например только что созданный чат) не возвращаются.
        title — первое сообщение пользователя (тема сессии); если его нет,
        fallback на имя сессии. model — из settings_json. message_count — число
        сообщений. limit=None → без ограничений.
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT s.id,
                       COALESCE(NULLIF(s.title, ''), (
                           SELECT json_extract(m2.message_json, '$.content')
                           FROM messages m2
                           WHERE m2.session_id = s.id
                             AND json_extract(m2.message_json, '$.role') = 'user'
                             AND json_extract(m2.message_json, '$.content') IS NOT NULL
                             AND json_extract(m2.message_json, '$.content') != ''
                           ORDER BY m2.seq
                           LIMIT 1
                       ), s.name) AS title_raw,
                       json_extract(s.settings_json, '$.model') AS model,
                       s.updated_at,
                       (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS msg_cnt
                FROM sessions s
                JOIN messages m ON m.session_id = s.id
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                [limit if limit is not None else -1, offset],
            ).fetchall()
        return [
            SessionInfo(
                id=r[0],
                title=_shorten(r[1]),
                model=r[2] if r[2] else None,
                updated_at=r[3],
                message_count=int(r[4]) if r[4] is not None else None,
            )
            for r in rows
        ]

    def get(self, session_id: str) -> SessionData | None:
        """Полное состояние сессии (включая неактивные ветки); None, если её нет."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT name, system_prompt, settings_json, summary, compacted_upto,
                       facts, active_branch, branches_json, scratchpad
                FROM sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            message_rows = self._conn.execute(
                "SELECT message_json FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        settings = AgentSettings.model_validate_json(row[2])
        history = [Message.model_validate_json(r[0]) for r in message_rows]
        active = row[6] or DEFAULT_BRANCH
        branches: dict[str, BranchState] = {}
        for state in json.loads(row[7] or "[]"):
            branch_name = str(state.get("name", "")).strip()
            if not branch_name or branch_name == active:
                continue
            branches[branch_name] = BranchState(
                history=[Message.model_validate(m) for m in state.get("history", [])],
                summary=state.get("summary"),
                compacted_upto=int(state.get("compacted_upto", 0)),
                facts={str(k): str(v) for k, v in state.get("facts", {}).items()},
            )
        return SessionData(
            settings=settings,
            system_prompt=row[1],
            name=row[0],
            summary=row[3] or None,
            compacted_upto=int(row[4] or 0),
            facts={str(k): str(v) for k, v in json.loads(row[5] or "{}").items()},
            scratchpad=row[8] or "",
            active_branch=active,
            branches=branches,
            history=history,
        )

    def delete(self, session_id: str) -> bool:
        """Удаляет сессию и её сообщения (CASCADE). True, если что-то удалено."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def set_title(self, session_id: str, title: str) -> bool:
        """Задаёт пользовательский заголовок сессии (описание).

        Заголовок хранится в отдельной колонке и переживает автоснапшоты
        (`snapshot` его не перезаписывает). Пустая строка сбрасывает на
        автовывод (первое сообщение пользователя / имя). True, если изменён.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?", (title, session_id)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def export(self, session_id: str, path: Path | str) -> Path:
        """Экспортирует сессию в jsonl (формат совместим со старыми /save-файлами)."""
        data = self.get(session_id)
        if data is None:
            raise KeyError(f"сессия не найдена: {session_id}")
        return save_session(
            path,
            settings=data.settings,
            system_prompt=data.system_prompt,
            name=data.name,
            summary=data.summary,
            compacted_upto=data.compacted_upto,
            history=data.history,
            facts=data.facts,
            scratchpad=data.scratchpad,
            active_branch=data.active_branch,
            branches=data.branches,
        )

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
