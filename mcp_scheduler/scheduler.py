"""Фоновый цикл планировщика: тикает, находит «созревшие» задания и выполняет их.

`Scheduler` владеет `SchedulerStore` и запускается из lifespan MCP-сервера.
На каждом тике (`--tick`, по умолчанию 1 сек) берутся включённые задания,
у которых `next_run <= now`, и исполняются. Пропущенные интервалы из-за задержки
(простой/глубокий сон) не догоняются — следующий `next_run` пересчитывается
от момента фактического срабатывания.

Три вида заданий:
- `reminder` — пишет запись в `runs` и отключается (одноразовый);
- `collect` — собирает точку данных (по `url` через HTTP, либо статический payload);
- `summary` — агрегирует точки данных за период `(last_run, now]` в отчёт.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx

from mcp_scheduler.aggregate import aggregate
from mcp_scheduler.store import Job, SchedulerStore

if TYPE_CHECKING:
    from agent.config.schema import Config
    from agent.llm.client import LLMClient


def _now() -> datetime:
    return datetime.now(UTC)


async def _collect(job: Job) -> dict[str, object]:
    """Собирает одну точку данных: по URL (HTTP GET → JSON) или статический payload."""
    url = job.payload.get("url")
    if url:
        async with httpx.AsyncClient() as client:
            resp = await client.get(str(url), timeout=10)
            try:
                data = resp.json()
            except ValueError:
                data = {"text": resp.text}
        return {"source": str(url), "status": resp.status_code, "data": data}
    return {"data": job.payload}


class Scheduler:
    """Фоновый цикл исполнения заданий планировщика."""

    def __init__(
        self,
        store: SchedulerStore,
        config: Config | None = None,
        llm: LLMClient | None = None,
        tick: float = 1.0,
        notify_url: str | None = None,
    ) -> None:
        self._store = store
        self._config = config
        self._llm = llm
        self._tick = tick
        self._notify_url = notify_url
        self._task: asyncio.Task[None] | None = None
        self._bg_tasks: set[asyncio.Task[None]] = set()

    # --- управление циклом ---

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await self.tick_once()
            await asyncio.sleep(self._tick)

    # --- исполнение ---

    async def tick_once(self) -> None:
        """Один цикл: сработать все «созревшие» задания (без догона пропущенных)."""
        now = _now()
        for job in self._store.due_jobs(now):
            await self._run_job(job, now)

    async def run_now(self, name: str) -> None:
        """Немедленно исполнить задание по имени (иначе ValueError)."""
        job = self._store.get_job(name)
        if job is None:
            raise ValueError(f"задание '{name}' не найдено")
        await self._run_job(job, _now())

    async def _run_job(self, job: Job, now: datetime) -> None:
        try:
            if job.kind == "reminder":
                summary = {"note": job.payload.get("message", "")}
                self._store.complete_run(job, now, summary)
            elif job.kind == "collect":
                point = await _collect(job)
                self._store.append_data(str(job.payload.get("data_kind", job.name)), point)
                summary = {"collected": True}
                self._store.complete_run(job, now, summary)
            else:  # summary
                summary = await self._summarize(job, now)
                self._store.complete_run(job, now, summary)
        except Exception as exc:  # сбой задания не должен ронять цикл
            summary = {"error": str(exc)}
            self._store.complete_run(job, now, summary)
        with suppress(Exception):
            await self._notify("job_ran", job, summary)

    async def _notify(
        self, event: str, job: Job, summary: dict[str, object] | None = None
    ) -> None:
        """Best-effort уведомление владельца задания о событии (после исполнения)."""
        if not self._notify_url:
            return
        payload: dict[str, object] = {"event": event, "job": job.to_dict()}
        if summary is not None:
            payload["summary"] = summary
        async with httpx.AsyncClient() as client:
            await client.post(self._notify_url, json=payload, timeout=5)

    def notify_added(self, job: Job) -> None:
        """Сообщает о создании задания (фоновый POST, если задан notify_url)."""
        self._spawn_notify("job_added", job)

    def notify_removed(self, job: Job) -> None:
        """Сообщает об удалении задания (фоновый POST, если задан notify_url)."""
        self._spawn_notify("job_removed", job)

    def _spawn_notify(self, event: str, job: Job) -> None:
        """Запускает уведомление фоном; без running-loop это no-op (тесты)."""
        if not self._notify_url:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        task = asyncio.create_task(self._notify(event, job))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _summarize(self, job: Job, now: datetime) -> dict[str, object]:
        """Агрегирует точки за `(last_run, now]`; добавляет LLM-сводку при возможности."""
        data_kind = str(job.payload.get("data_kind", job.name))
        points = self._store.query_data(data_kind, since=job.last_run, to=_iso(now))
        op = str(job.payload.get("op", "count"))
        agg = aggregate(points, op)
        result: dict[str, object] = {"kind": data_kind, "aggregate": agg, "points": len(points)}
        if job.payload.get("llm") and self._config and self._llm:
            text = await self._llm_summary(agg, job)
            if text:
                result["llm"] = text
        return result

    async def _llm_summary(self, agg: dict[str, object] | None, job: Job) -> str:
        """Природно-языковой вывод через LLM (на основе правил агрегации)."""
        from agent.core.message import ChatRequest, Message, Role

        assert self._config is not None
        assert self._llm is not None
        model_id = str(job.payload.get("model") or self._config.default_model)
        provider, model = self._config.resolve_model(model_id)
        system = "Ты — аналитик данных. На основе сводки данных напиши краткий природный вывод."
        user = (json.dumps(agg or {}, ensure_ascii=False) if agg else "нет данных") + (
            "\n\nНапиши краткую сводку по этим данным."
        )
        request = ChatRequest(
            model=model,
            messages=[
                Message(role=Role.SYSTEM, content=system),
                Message(role=Role.USER, content=user),
            ],
            stream=True,
        )
        parts: list[str] = []
        async for chunk in self._llm.astream(request, provider.api_base, provider.api_key):
            if chunk.content:
                parts.append(chunk.content)
        return "".join(parts)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")
