"""Планировщик end-to-end: срабатывание по тику, агрегация и MCP-инструменты.

Часть тестов гоняет `Scheduler` напрямую (без транспорта), часть — через
McpAdapter к реальному stdio-подпроцессу `agent-mcp-scheduler`.
"""

import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from agent.config.schema import McpServer
from agent.tools.mcp import McpAdapter
from mcp_scheduler.scheduler import Scheduler
from mcp_scheduler.store import SchedulerStore


def dt(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def force_due(store: SchedulerStore, name: str) -> None:
    """Приводит `next_run` задания в прошлое (для срабатывания по тику)."""
    with store._lock:
        store._conn.execute(
            "UPDATE jobs SET next_run = '2000-01-01T00:00:00+00:00' WHERE name = ?",
            (name,),
        )
        store._conn.commit()


async def test_collect_via_tick_records_data_point() -> None:
    store = SchedulerStore(Path(":memory:"))
    sched = Scheduler(store, tick=3600)
    store.add_job("collect", "c", {"type": "interval", "seconds": 30}, {"data_kind": "m"})
    force_due(store, "c")
    await sched.tick_once()
    assert store.query_data("m") != []
    job = store.get_job("c")
    assert job is not None
    assert job.last_run is not None
    assert job.next_run is not None  # интервал пересчитан на будущее


async def test_summary_job_aggregates_on_run() -> None:
    store = SchedulerStore(Path(":memory:"))
    sched = Scheduler(store, tick=3600)
    store.append_data("metrics", {"value": 5})
    store.append_data("metrics", {"value": 7})
    store.add_job(
        "summary",
        "daily",
        {"type": "interval", "seconds": 3600},
        {"data_kind": "metrics", "op": "sum"},
    )
    await sched.run_now("daily")
    latest = store.latest_run("daily")
    assert latest is not None
    assert latest["summary"]["aggregate"]["value"] == 12
    assert latest["summary"]["points"] == 2


async def test_reminder_fires_note() -> None:
    store = SchedulerStore(Path(":memory:"))
    sched = Scheduler(store, tick=3600)
    store.add_job(
        "reminder",
        "r",
        {"type": "at", "at": "2026-09-25T00:00:00Z"},
        {"message": "сделать отчёт"},
    )
    force_due(store, "r")
    await sched.tick_once()
    latest = store.latest_run("r")
    assert latest is not None
    assert latest["summary"]["note"] == "сделать отчёт"
    assert store.get_job("r").last_run is not None


@asynccontextmanager
async def connected(sched_spec: McpServer) -> AsyncIterator[McpAdapter]:
    adapter = McpAdapter("scheduler")
    await adapter.connect(sched_spec)
    try:
        yield adapter
    finally:
        await adapter.close()


def make_scheduler_spec(db: Path) -> McpServer:
    return McpServer(
        transport="stdio",
        command=sys.executable,
        args=["-m", "mcp_scheduler.server", "--stdio", "--db", str(db)],
    )


async def test_mcp_adapter_pulls_schedule_tools(tmp_path: Path) -> None:
    db = tmp_path / "sched.db"
    spec = make_scheduler_spec(db)
    async with connected(spec) as adapter:
        tools = await adapter.list_tools()
        names = [t["name"] for t in tools]
        assert "schedule_add" in names
        assert "schedule_list" in names
        result = await adapter.call_tool(
            "schedule_add",
            {"kind": "collect", "name": "c", "trigger": {"type": "interval", "seconds": 30}},
        )
        assert result.is_error is False
        assert json.loads(result.output)["name"] == "c"
