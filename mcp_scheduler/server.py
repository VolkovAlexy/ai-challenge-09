"""MCP-сервер планировщика (`scheduler`): задания по расписанию, сбор и сводки.

Сервер регистрирует инструменты `schedule_add`, `schedule_list`, `schedule_remove`,
`schedule_run`, `data_append`, `data_query`, `summary_report` и даёт агенту
возможность отложенного/периодического исполнения (напоминания, сбор данных,
регулярная сводка). Данные живут в SQLite (переживают перезапуск), фоновая
логика — в `Scheduler` (запускается из lifespan).

Запуск (http-транспорт, его использует конфиг агента):
    uv run agent-mcp-scheduler --port 8898 --db schedules/scheduler.db

Для LLM-сводок серверу нужен `config.json` и `LLMClient` (передаются в
`make_server`); без них сводка остаётся чисто правиловой.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.applications import Starlette

from mcp_scheduler.aggregate import aggregate
from mcp_scheduler.scheduler import Scheduler
from mcp_scheduler.store import JOB_KINDS, Job, SchedulerStore

DEFAULT_DB = "schedules/scheduler.db"
DEFAULT_CONFIG = "config.json"
DEFAULT_HTTP_PORT = 8898


class SchedulerService:
    """Логика MCP-инструментов планировщика (без транспорта — тестируется напрямую)."""

    def __init__(self, store: SchedulerStore, scheduler: Scheduler) -> None:
        self._store = store
        self._scheduler = scheduler

    def schedule_add(
        self,
        kind: str,
        name: str,
        trigger: dict[str, Any],
        payload: dict[str, Any] | None = None,
        url: str | None = None,
    ) -> str:
        """Создаёт задание; возвращает JSON созданного задания."""
        merged: dict[str, Any] = dict(payload or {})
        if url:
            merged["url"] = url
        job = self._store.add_job(kind, name, trigger, merged)
        return json.dumps(_job_to_dict(job), ensure_ascii=False)

    def schedule_list(self) -> str:
        """Список всех заданий (JSON-массив)."""
        return json.dumps(
            [_job_to_dict(job) for job in self._store.list_jobs()], ensure_ascii=False
        )

    def schedule_remove(self, name: str) -> str:
        """Удаляет задание по имени; `removed` — False, если задания нет."""
        return json.dumps({"removed": self._store.remove_job(name)}, ensure_ascii=False)

    async def schedule_run(self, name: str) -> str:
        """Исполняет задание немедленно; возвращает последнюю сводку запуска."""
        await self._scheduler.run_now(name)
        latest = self._store.latest_run(name) or {}
        return json.dumps(latest, ensure_ascii=False)

    def data_append(self, kind: str, payload: dict[str, Any]) -> str:
        """Добавляет точку данных (для последующей агрегации/сводки)."""
        point_id = self._store.append_data(kind, payload)
        return json.dumps({"id": point_id, "kind": kind}, ensure_ascii=False)

    def data_query(
        self, kind: str, op: str = "count", since: str | None = None, to: str | None = None
    ) -> str:
        """Агрегат по точкам данных заданного вида (окно — по created_at)."""
        points = self._store.query_data(kind, since=since, to=to)
        return json.dumps(aggregate(points, op), ensure_ascii=False)

    def summary_report(self, name: str) -> str:
        """Последняя сводка задания (JSON); {} если запусков ещё не было."""
        latest = self._store.latest_run(name) or {}
        return json.dumps(latest, ensure_ascii=False)


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "name": job.name,
        "trigger": job.trigger,
        "payload": job.payload,
        "enabled": job.enabled,
        "created_at": job.created_at,
        "last_run": job.last_run,
        "next_run": job.next_run,
    }


def make_server(
    db: str,
    config: Any = None,
    llm: Any = None,
    tick: float = 1.0,
) -> MCPServer:
    """Создаёт MCP-сервер планировщика над `SchedulerStore` (путь `db`).

    `config`/`llm` необязательны; их наличие включает LLM-сводки для summary.
    Lifespan запускает/останавливает фоновый цикл `Scheduler`.
    """
    store = SchedulerStore(db)
    scheduler = Scheduler(store, config=config, llm=llm, tick=tick)
    service = SchedulerService(store, scheduler)

    @asynccontextmanager
    async def _lifespan(server: MCPServer):
        await scheduler.start()
        try:
            yield
        finally:
            await scheduler.stop()

    server = MCPServer(
        "scheduler",
        version="0.1.0",
        instructions="Планировщик заданий: напоминания, периодический сбор и сводки.",
        lifespan=_lifespan,
    )

    @server.tool()
    async def schedule_add(
        kind: Annotated[str, Field(description=f"Вид задания: {', '.join(JOB_KINDS)}")],
        name: Annotated[str, Field(description="Уникальное имя задания")],
        trigger: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Правило расписания (dict): "
                    '{"type": "at", "at": "<ISO>"} — один раз в момент '
                    '(напр. "2026-09-24T12:00:00+00:00"); '
                    '{"type": "interval", "seconds": 60} — каждые N секунд; '
                    '{"type": "cron", "expr": "0 9 * * 1"} — cron "m h dom mon dow". '
                    "Реальные числа: minutes не существует, нужен seconds."
                )
            ),
        ],
        payload: Annotated[
            dict[str, Any] | None, Field(default=None, description="Доп. параметры")
        ] = None,
        url: Annotated[
            str | None, Field(default=None, description="URL для сбора данных")
        ] = None,
    ) -> str:
        """Создаёт отложенное/периодическое задание и возвращает его описание."""
        return service.schedule_add(kind, name, trigger, payload=payload, url=url)

    @server.tool()
    async def schedule_list() -> str:
        """Список всех заданий планировщика."""
        return service.schedule_list()

    @server.tool()
    async def schedule_remove(
        name: Annotated[str, Field(description="Имя задания для удаления")],
    ) -> str:
        """Удаляет задание по имени."""
        return service.schedule_remove(name)

    @server.tool()
    async def schedule_run(
        name: Annotated[str, Field(description="Имя задания для немедленного запуска")],
    ) -> str:
        """Исполняет задание сейчас, не дожидаясь расписания."""
        return await service.schedule_run(name)

    @server.tool()
    async def data_append(
        kind: Annotated[str, Field(description="Вид данных (тег для агрегации)")],
        payload: Annotated[dict[str, Any], Field(description="Содержимое точки данных")],
    ) -> str:
        """Добавляет точку данных — агенту не нужно держать её в памяти."""
        return service.data_append(kind, payload)

    @server.tool()
    async def data_query(
        kind: Annotated[str, Field(description="Вид данных")],
        op: Annotated[
            str, Field(default="count", description="count|sum|avg|min|max")
        ] = "count",
        since: Annotated[
            str | None, Field(default=None, description="created_at >= (ISO)")
        ] = None,
        to: Annotated[
            str | None, Field(default=None, description="created_at <= (ISO)")
        ] = None,
    ) -> str:
        """Агрегирует точки данных заданного вида."""
        return service.data_query(kind, op=op, since=since, to=to)

    @server.tool()
    async def summary_report(
        name: Annotated[str, Field(description="Имя summary-задания")],
    ) -> str:
        """Последняя сводка задания (агрегат + опциональная LLM-сводка)."""
        return service.summary_report(name)

    return server


def make_http_app(
    db: str, config: Any = None, llm: Any = None, tick: float = 1.0
) -> Starlette:
    """Streamable HTTP ASGI-приложение MCP-сервера планировщика."""
    return make_server(db, config=config, llm=llm, tick=tick).streamable_http_app()


def main(argv: list[str] | None = None) -> int:
    """Точка входа `agent-mcp-scheduler`.

    По умолчанию — Streamable HTTP на `--port` (8898). С `--stdio` — stdio.
    Для LLM-сводок читает `config.json` (`--config`).
    """
    parser = argparse.ArgumentParser(description="MCP-сервер планировщика заданий")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP-порт")
    parser.add_argument("--db", default=DEFAULT_DB, help="путь к SQLite-хранилищу")
    parser.add_argument("--tick", type=float, default=1.0, help="период тика планировщика (сек)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="путь к config.json")
    parser.add_argument("--stdio", action="store_true", help="запустить по stdio вместо HTTP")
    args = parser.parse_args(argv)

    from agent.config.store import load_config
    from agent.llm.client import LLMClient

    config = load_config(args.config)
    llm = LLMClient()

    if args.stdio:
        asyncio.run(make_server(args.db, config, llm, args.tick).run_stdio_async())
        return 0
    uvicorn.run(
        make_http_app(args.db, config, llm, args.tick),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
