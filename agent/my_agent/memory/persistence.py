"""Хранение сессий в SQLite: автосохранение без риска потери при закрытии/сбое.

`SessionStore` — один файл (по умолчанию `sessions/sessions.db`). Две таблицы:
`sessions` (имя, системный промпт, саммари, его длина, настройки) и `messages`
(история по seq). Настройки и сообщения хранятся как JSON (pydantic `model_dump_json`),
чтобы схемы оставались forward-compatible. `snapshot` — атомарный upsert в
одной транзакции (полная замена истории), поэтому torn-write невозможен.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from my_agent.config.schema import AgentSettings
from my_agent.core.message import Message
from my_agent.memory.session import SessionData, save_session

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
]


@dataclass
class SessionInfo:
    """Строка в палитре /session: метаданные для вывода (без истории)."""

    id: str
    title: str
    updated_at: str


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
        self._conn = sqlite3.connect(self._path)
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
    ) -> None:
        """Атомарный upsert-снапшот: метаданные + полная замена истории."""
        now = _now()
        settings_json = settings.model_dump_json()
        rows = [(session_id, seq, message.model_dump_json()) for seq, message in enumerate(history)]
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO sessions
                    (id, name, system_prompt, settings_json, summary, compacted_upto,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (id) DO UPDATE SET
                    name = excluded.name,
                    system_prompt = excluded.system_prompt,
                    settings_json = excluded.settings_json,
                    summary = excluded.summary,
                    compacted_upto = excluded.compacted_upto,
                    updated_at = excluded.updated_at
                """,
                (
                    session_id,
                    name,
                    system_prompt,
                    settings_json,
                    summary or "",
                    compacted_upto,
                    now,
                    now,
                ),
            )
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.executemany(
                "INSERT INTO messages (session_id, seq, message_json) VALUES (?, ?, ?)",
                rows,
            )

    def list(self) -> list[SessionInfo]:
        """Непустые сессии, newest-first (по updated_at).

        Пустые (0 сообщений, например только что созданный чат) не возвращаются.
        title — первое сообщение пользователя (тема сессии); если его нет,
        fallback на имя сессии.
        """
        rows = self._conn.execute(
            """
            SELECT s.id,
                   COALESCE((
                       SELECT json_extract(m2.message_json, '$.content')
                       FROM messages m2
                       WHERE m2.session_id = s.id
                         AND json_extract(m2.message_json, '$.role') = 'user'
                         AND json_extract(m2.message_json, '$.content') IS NOT NULL
                         AND json_extract(m2.message_json, '$.content') != ''
                       ORDER BY m2.seq
                       LIMIT 1
                   ), s.name) AS title_raw,
                   s.updated_at
            FROM sessions s
            JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """
        ).fetchall()
        return [SessionInfo(id=r[0], title=_shorten(r[1]), updated_at=r[2]) for r in rows]

    def get(self, session_id: str) -> SessionData | None:
        """Полное состояние сессии; None, если её нет."""
        row = self._conn.execute(
            """
            SELECT name, system_prompt, settings_json, summary, compacted_upto
            FROM sessions WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        settings = AgentSettings.model_validate_json(row[2])
        message_rows = self._conn.execute(
            "SELECT message_json FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        history = [Message.model_validate_json(r[0]) for r in message_rows]
        return SessionData(
            settings=settings,
            system_prompt=row[1],
            name=row[0],
            summary=row[3] or None,
            compacted_upto=int(row[4] or 0),
            history=history,
        )

    def delete(self, session_id: str) -> bool:
        """Удаляет сессию и её сообщения (CASCADE). True, если что-то удалено."""
        cur = self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
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
        )

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def close(self) -> None:
        self._conn.close()
