"""Хранение сессий в SQLite: автосохранение без риска потери при закрытии/сбое.

`SessionStore` — один файл (по умолчанию `sessions/sessions.db`). Две таблицы:
`sessions` (имя, системный промпт, саммари, его длина, настройки) и `messages`
(история по seq). Настройки и сообщения хранятся как JSON (pydantic `model_dump_json`),
чтобы схемы оставались forward-compatible. `snapshot` — атомарный upsert в
одной транзакции (полная замена истории), поэтому torn-write невозможен.
"""

from __future__ import annotations

import builtins
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
from agent.core.task import TaskState
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
    title TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS messages (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    message_json TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS session_schedule (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    scheduled_count INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS longterm_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_profiles (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    PRIMARY KEY (project_id, profile_id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions (updated_at);
CREATE INDEX IF NOT EXISTS idx_longterm_project ON longterm_entries (project_id);
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
    "ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN active_profile_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE sessions ADD COLUMN task TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE sessions ADD COLUMN invariants TEXT NOT NULL DEFAULT '[]'",
    # создаётся ПОСЛЕ добавления column project_id — на старой БД индекс
    # не может существовать до наращивания схемы
    "CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions (project_id)",
]


@dataclass
class SessionInfo:
    """Строка в палитре /session: метаданные для вывода (без истории)."""

    id: str
    title: str
    updated_at: str
    model: str | None = None
    message_count: int | None = None
    project_id: str = ""
    has_scheduled: bool = False
    unread_notifications: int = 0


@dataclass
class ProjectInfo:
    """Проект: верхний уровень иерархии памяти (свои сессии и долгосрочная память)."""

    id: str
    name: str
    session_count: int = 0
    updated_at: str = ""


@dataclass
class ProfileInfo:
    """Глобальный профиль роли: системный промпт чата (обогащает/переопределяет базовый)."""

    id: str
    name: str
    content: str
    created_at: str = ""
    updated_at: str = ""


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
        invariants: list[str] | None = None,
        active_branch: str = DEFAULT_BRANCH,
        branches: dict[str, BranchState] | None = None,
        project_id: str = "",
        active_profile_id: str = "",
        task: TaskState | None = None,
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
                         facts, scratchpad, invariants, active_branch, branches_json, created_at,
                         updated_at, project_id, active_profile_id, task)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (id) DO UPDATE SET
                        name = excluded.name,
                        system_prompt = excluded.system_prompt,
                        settings_json = excluded.settings_json,
                        summary = excluded.summary,
                        compacted_upto = excluded.compacted_upto,
                        facts = excluded.facts,
                        scratchpad = excluded.scratchpad,
                        invariants = excluded.invariants,
                        active_branch = excluded.active_branch,
                        branches_json = excluded.branches_json,
                        updated_at = excluded.updated_at,
                        project_id = excluded.project_id,
                        active_profile_id = excluded.active_profile_id,
                        task = excluded.task
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
                    json.dumps(invariants or [], ensure_ascii=False),
                    active_branch,
                    json.dumps(inactive, ensure_ascii=False),
                    now,
                    now,
                    project_id,
                    active_profile_id,
                    json.dumps(task.to_dict(), ensure_ascii=False)
                    if task is not None and task.is_active
                    else "{}",
                ),
            )
            self._conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self._conn.executemany(
                "INSERT INTO messages (session_id, seq, message_json) VALUES (?, ?, ?)",
                rows,
            )

    def list(
        self,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[SessionInfo]:
        """Непустые сессии, newest-first (по updated_at).

        `project_id` — фильтр по проекту (None → все проекты). Пустые
        (0 сообщений, например только что созданный чат) не возвращаются.
        title — первое сообщение пользователя (тема сессии); если его нет,
        fallback на имя сессии. model — из settings_json. message_count — число
        сообщений. limit=None → без ограничений.
        """
        where = ""
        params: list[object] = []
        if project_id is not None:
            where = "WHERE s.project_id = ?"
            params.append(project_id)
        params.extend([limit if limit is not None else -1, offset])
        with self._lock:
            rows = self._conn.execute(
                f"""
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
                       (SELECT COUNT(*) FROM messages WHERE session_id = s.id) AS msg_cnt,
                       s.project_id,
                       COALESCE(sc.scheduled_count, 0) AS sched_cnt,
                       COALESCE(sc.unread, 0) AS unread
                FROM sessions s
                JOIN messages m ON m.session_id = s.id
                LEFT JOIN session_schedule sc ON sc.session_id = s.id
                {where}
                GROUP BY s.id
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [
            SessionInfo(
                id=r[0],
                title=_shorten(r[1]),
                model=r[2] if r[2] else None,
                updated_at=r[3],
                message_count=int(r[4]) if r[4] is not None else None,
                project_id=r[5] or "",
                has_scheduled=int(r[6] or 0) > 0,
                unread_notifications=int(r[7] or 0),
            )
            for r in rows
        ]

    def get(self, session_id: str) -> SessionData | None:
        """Полное состояние сессии (включая неактивные ветки); None, если её нет."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT name, system_prompt, settings_json, summary, compacted_upto,
                       facts, active_branch, branches_json, scratchpad, active_profile_id,
                       task, invariants
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
            invariants=[str(x) for x in json.loads(row[11] or "[]")],
            active_branch=active,
            branches=branches,
            history=history,
            active_profile_id=row[9] or "",
            task=TaskState.from_dict(json.loads(row[10] or "{}")),
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

    def get_project_id(self, session_id: str) -> str:
        """Проект, которому принадлежит сессия ('' — неизвестно/легаси)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT project_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row[0] or "" if row else ""

    # --- планировщик: флаги сессии (иконка ⏰ и badge непрочитанного) ---

    def incr_scheduled(self, session_id: str) -> None:
        """Добавляет активное задание планировщика к сессии (счётчик)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO session_schedule (session_id, scheduled_count, unread)
                VALUES (?, 1, 0)
                ON CONFLICT (session_id) DO UPDATE SET
                    scheduled_count = session_schedule.scheduled_count + 1
                """,
                (session_id,),
            )

    def decr_scheduled(self, session_id: str) -> None:
        """Убирает одно активное задание планировщика от сессии (счётчик)."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO session_schedule (session_id, scheduled_count, unread)
                VALUES (?, 0, 0)
                ON CONFLICT (session_id) DO UPDATE SET
                    scheduled_count = max(session_schedule.scheduled_count - 1, 0)
                """,
                (session_id,),
            )

    def incr_unread(self, session_id: str, delta: int = 1) -> None:
        """Увеличивает счётчик непрочитанных результатов задания для сессии."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO session_schedule (session_id, scheduled_count, unread)
                VALUES (?, 0, ?)
                ON CONFLICT (session_id) DO UPDATE SET
                    unread = session_schedule.unread + ?
                """,
                (session_id, delta, delta),
            )

    def clear_unread(self, session_id: str) -> None:
        """Сбрасывает счётчик непрочитанных результатов (сессия открыта)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE session_schedule SET unread = 0 WHERE session_id = ?",
                (session_id,),
            )

    def unread_count(self, session_id: str) -> int:
        """Число непрочитанных результатов задания (для badge)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT unread FROM session_schedule WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def append_notification(self, session_id: str, message_json: str) -> bool:
        """Дописывает результат задания в историю закрытой сессии.

        Используется, когда сессия не загружена (нет live-агента): сообщение
        ложится в `messages` с `seq = max+1`, чтобы при открытии сессии оно
        было видно. Возвращает True, если сессия существует и запись добавлена.
        """
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                return False
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO messages (session_id, seq, message_json) VALUES (?, ?, ?)",
                (session_id, seq, message_json),
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
            )
            self._conn.execute(
                "INSERT INTO session_schedule (session_id, scheduled_count, unread)"
                " VALUES (?, 0, 0) ON CONFLICT (session_id) DO NOTHING",
                (session_id,),
            )
        return True

    def create_project(self, name: str) -> ProjectInfo:
        """Создаёт проект. Имя схлопывается в одну строку; пустое — ValueError."""
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("имя проекта не может быть пустым")
        project_id = self.new_id()
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO projects (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (project_id, cleaned, now, now),
            )
        return ProjectInfo(id=project_id, name=cleaned, updated_at=now)

    def ensure_project(self, project_id: str, name: str) -> None:
        """Создаёт проект с заданным id, если его ещё нет (idempotent)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO projects (id, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (project_id, name, _now(), _now()),
            )

    def list_projects(self) -> builtins.list[ProjectInfo]:
        """Все проекты с числом сессий, newest-first (по updated_at)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT p.id, p.name, p.updated_at,
                       (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS cnt
                FROM projects p
                ORDER BY p.updated_at DESC, p.name
                """
            ).fetchall()
        return [
            ProjectInfo(id=r[0], name=r[1], updated_at=r[2], session_count=int(r[3] or 0))
            for r in rows
        ]

    def get_project(self, project_id: str) -> ProjectInfo | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.id, p.name, p.updated_at,
                       (SELECT COUNT(*) FROM sessions s WHERE s.project_id = p.id) AS cnt
                FROM projects p WHERE p.id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return None
        return ProjectInfo(
            id=row[0], name=row[1], session_count=int(row[3] or 0), updated_at=row[2]
        )

    def rename_project(self, project_id: str, name: str) -> bool:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("имя проекта не может быть пустым")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
                (cleaned, _now(), project_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def delete_project(self, project_id: str) -> bool:
        """Удаляет проект, его сессии и долгосрочную память. True, если он был."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM longterm_entries WHERE project_id = ?", (project_id,))
            self._conn.execute("DELETE FROM sessions WHERE project_id = ?", (project_id,))
            cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return cur.rowcount > 0

    # --- долгосрочная память проекта (SQL) ---

    def list_longterm(self, project_id: str) -> builtins.list[str]:
        """Записи долгосрочной памяти проекта (по порядку добавления)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM longterm_entries WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def append_longterm(self, project_id: str, text: str) -> str:
        """Добавляет запись (одна строка). Возвращает нормализованный текст."""
        entry = " ".join(text.split())
        if not entry:
            raise ValueError("запись памяти не может быть пустой")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO longterm_entries (project_id, text, created_at) VALUES (?, ?, ?)",
                (project_id, entry, _now()),
            )
        return entry

    def remove_longterm(self, project_id: str, index: int) -> str:
        """Удаляет запись по индексу (0-based). IndexError — индекса нет."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text FROM longterm_entries WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
            if index < 0 or index >= len(rows):
                raise IndexError(f"записи памяти с индексом {index} нет")
            entry_id, text = rows[index]
            self._conn.execute("DELETE FROM longterm_entries WHERE id = ?", (entry_id,))
            self._conn.commit()
        return str(text)

    def update_longterm(self, project_id: str, index: int, text: str) -> str:
        """Заменяет запись по индексу; возвращает новый текст (нормализован)."""
        entry = " ".join(text.split())
        if not entry:
            raise ValueError("запись памяти не может быть пустой")
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM longterm_entries WHERE project_id = ? ORDER BY id",
                (project_id,),
            ).fetchall()
            if index < 0 or index >= len(rows):
                raise IndexError(f"записи памяти с индексом {index} нет")
            entry_id = rows[index][0]
            self._conn.execute(
                "UPDATE longterm_entries SET text = ? WHERE id = ?", (entry, entry_id)
            )
            self._conn.commit()
        return entry

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
            invariants=data.invariants,
            task=data.task,
            active_branch=data.active_branch,
            branches=data.branches,
            active_profile_id=data.active_profile_id,
        )

    # --- профили (глобальный пул, привязка к проекту) ---

    def create_profile(self, name: str, content: str) -> ProfileInfo:
        """Создаёт глобальный профиль. Имя пустое — ValueError."""
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("имя профиля не может быть пустым")
        profile_id = self.new_id()
        now = _now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO profiles (id, name, content, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (profile_id, cleaned, content, now, now),
            )
        return ProfileInfo(id=profile_id, name=cleaned, content=content, updated_at=now)

    def list_profiles(self) -> builtins.list[ProfileInfo]:
        """Все глобальные профили (новые раньше — по updated_at)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, content, created_at, updated_at"
                " FROM profiles ORDER BY updated_at DESC, name"
            ).fetchall()
        return [
            ProfileInfo(id=r[0], name=r[1], content=r[2], created_at=r[3], updated_at=r[4])
            for r in rows
        ]

    def get_profile(self, profile_id: str) -> ProfileInfo | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, content, created_at, updated_at"
                " FROM profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
        if row is None:
            return None
        return ProfileInfo(
            id=row[0], name=row[1], content=row[2], created_at=row[3], updated_at=row[4]
        )

    def update_profile(self, profile_id: str, name: str, content: str) -> ProfileInfo | None:
        """Переименовывает/меняет текст профиля; None — профиля нет."""
        existing = self.get_profile(profile_id)
        if existing is None:
            return None
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("имя профиля не может быть пустым")
        with self._lock:
            self._conn.execute(
                "UPDATE profiles SET name = ?, content = ?, updated_at = ? WHERE id = ?",
                (cleaned, content, _now(), profile_id),
            )
            self._conn.commit()
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id: str) -> bool:
        """Удаляет профиль: снимает привязки к проектам и у сессий. True, если он был."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM project_profiles WHERE profile_id = ?", (profile_id,))
            self._conn.execute(
                "UPDATE sessions SET active_profile_id = '' WHERE active_profile_id = ?",
                (profile_id,),
            )
            cur = self._conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        return cur.rowcount > 0

    def set_project_profiles(
        self, project_id: str, profile_ids: builtins.list[str]
    ) -> None:
        """Заменяет набор профилей проекта (полная замена связей)."""
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM project_profiles WHERE project_id = ?", (project_id,)
            )
            for profile_id in profile_ids:
                self._conn.execute(
                    "INSERT OR IGNORE INTO project_profiles (project_id, profile_id)"
                    " VALUES (?, ?)",
                    (project_id, profile_id),
                )

    def list_project_profiles(self, project_id: str) -> builtins.list[str]:
        """Профили, привязанные к проекту (в порядке добавления)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT profile_id FROM project_profiles"
                " WHERE project_id = ? ORDER BY rowid",
                (project_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
