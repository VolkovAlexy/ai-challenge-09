"""SQLite-хранилище планировщика: задания (`jobs`), запуски (`runs`), точки данных.

Схема в духе `agent.memory.persistence` (sqlite3 + threading.Lock + `_SCHEMA`).
Три таблицы: `jobs` (имя уникально), `runs` (история срабатываний по job),
`data_points` (сырые данные для агрегации, с собственным `kind`).
JSON-поля (trigger/payload/summary) хранятся строками, чтобы схема оставалась
forward-compatible.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mcp_scheduler.triggers import next_run_at, parse_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL UNIQUE,
    trigger_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run TEXT,
    next_run TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ran_at TEXT NOT NULL,
    summary_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS data_points (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_next ON jobs (next_run);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs (job_id);
CREATE INDEX IF NOT EXISTS idx_data_kind ON data_points (kind);
"""

JOB_KINDS = ("reminder", "collect", "summary")


@dataclass
class Job:
    """Одно задание планировщика."""

    id: str
    kind: str
    name: str
    trigger: dict[str, object]
    payload: dict[str, object]
    enabled: bool
    created_at: str
    last_run: str | None
    next_run: str | None


def _now() -> str:
    # фиксированная длина ISO-строк: корректная лексикографическая сортировка (ORDER BY)
    return datetime.now(UTC).isoformat(timespec="microseconds")


class SchedulerStore:
    """SQLite-хранилище заданий, запусков и точек данных планировщика."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @staticmethod
    def new_id() -> str:
        return uuid4().hex

    # --- задания ---

    def add_job(
        self,
        kind: str,
        name: str,
        trigger: dict[str, object],
        payload: dict[str, object] | None = None,
    ) -> Job:
        """Создаёт задание. Имя уникально (дубликат — ValueError). `enabled` по умолчанию True."""
        if kind not in JOB_KINDS:
            raise ValueError(f"неизвестный вид задания '{kind}' (доступно: {', '.join(JOB_KINDS)})")
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValueError("имя задания не может быть пустым")
        if next_run_at(trigger, datetime.now(UTC)) is None:
            raise ValueError("некорректный trigger: для него нет будущего момента срабатывания")
        job_id = self.new_id()
        now = _now()
        next_run = self._next_iso(trigger, now)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO jobs
                        (id, kind, name, trigger_json, payload_json, enabled,
                         created_at, last_run, next_run)
                        VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?)
                    """,
                    (
                        job_id,
                        kind,
                        cleaned,
                        json.dumps(trigger, ensure_ascii=False),
                        json.dumps(payload or {}, ensure_ascii=False),
                        now,
                        next_run,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"задание с именем '{cleaned}' уже существует") from exc
            self._conn.commit()
        return self.get_job(cleaned)  # type: ignore[return-value]

    def get_job(self, name: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE name = ?", (name,)).fetchone()
        return self._row_to_job(row) if row else None

    def list_jobs(self) -> list[Job]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at").fetchall()
        return [self._row_to_job(row) for row in rows]

    def remove_job(self, name: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE name = ?", (name,))
            self._conn.commit()
        return cur.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET enabled = ? WHERE name = ?", (1 if enabled else 0, name)
            )
            self._conn.commit()
        return cur.rowcount > 0

    def due_jobs(self, now: datetime) -> list[Job]:
        """Включённые задания, у которых `next_run` наступил (<= `now`)."""
        now_iso = _iso_string(now)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                (now_iso,),
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def complete_run(
        self, job: Job, ran_at: datetime, summary: dict[str, object] | None = None
    ) -> str:
        """Фиксирует срабатывание: добавляет запись в `runs` и пересчитывает `next_run`.

        Если для `next_run` будущего момента нет (например, одноразовый `at`),
        задание отключается (`enabled = 0`). Возвращает id записи запуска.
        """
        run_id = self.new_id()
        ran_at_iso = _iso_string(ran_at)
        next_iso = self._next_iso(job.trigger, ran_at_iso)
        enabled = 1 if next_iso is not None else 0
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs (id, job_id, ran_at, summary_json) VALUES (?, ?, ?, ?)",
                (run_id, job.id, ran_at_iso, json.dumps(summary or {}, ensure_ascii=False)),
            )
            self._conn.execute(
                "UPDATE jobs SET last_run = ?, next_run = ?, enabled = ? WHERE id = ?",
                (ran_at_iso, next_iso, enabled, job.id),
            )
        return run_id

    # --- точки данных ---

    def append_data(self, kind: str, payload: dict[str, object]) -> str:
        point_id = self.new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO data_points (id, kind, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (point_id, kind, json.dumps(payload, ensure_ascii=False), _now()),
            )
            self._conn.commit()
        return point_id

    def query_data(
        self, kind: str, since: str | None = None, to: str | None = None
    ) -> list[dict[str, object]]:
        """Точки данных заданного вида; окно по `created_at` (ISO, включительно)."""
        query = "SELECT payload_json FROM data_points WHERE kind = ?"
        params: list[object] = [kind]
        if since is not None:
            query += " AND created_at >= ?"
            params.append(since)
        if to is not None:
            query += " AND created_at <= ?"
            params.append(to)
        query += " ORDER BY created_at"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [json.loads(row[0]) for row in rows]

    # --- запуски ---

    def latest_run(self, name: str) -> dict[str, object] | None:
        """Последний запуск задания (по `ran_at`), либо None."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.ran_at, r.summary_json FROM runs r
                JOIN jobs j ON j.id = r.job_id
                WHERE j.name = ? ORDER BY r.ran_at DESC LIMIT 1
                """,
                (name,),
            ).fetchone()
        if row is None:
            return None
        return {"ran_at": row[0], "summary": json.loads(row[1])}

    # --- служебное ---

    def _next_iso(self, trigger: dict[str, object], base_iso: str) -> str | None:
        base = parse_iso(base_iso)
        if base is None:
            return None
        nxt = next_run_at(trigger, base)
        return _iso_string(nxt) if nxt is not None else None

    def _row_to_job(self, row: sqlite3.Row | tuple[object, ...]) -> Job:
        return Job(
            id=str(row[0]),
            kind=str(row[1]),
            name=str(row[2]),
            trigger=json.loads(str(row[3])),
            payload=json.loads(str(row[4])),
            enabled=bool(row[5]),
            created_at=str(row[6]),
            last_run=str(row[7]) if row[7] is not None else None,
            next_run=str(row[8]) if row[8] is not None else None,
        )

    def count_jobs(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _iso_string(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")
