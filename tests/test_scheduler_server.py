"""Планировщик как MCP-сервер: SchedulerService и `make_server` (list/call round-trip)."""

import asyncio
import json
from pathlib import Path

import httpx

from mcp_scheduler.scheduler import Scheduler
from mcp_scheduler.server import SchedulerService, make_server
from mcp_scheduler.store import SchedulerStore


def run_async(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]



def make_service() -> SchedulerService:
    store = SchedulerStore(Path(":memory:"))
    return SchedulerService(store, Scheduler(store, tick=3600))


def test_schedule_add_list_remove() -> None:
    svc = make_service()
    added = json.loads(svc.schedule_add("collect", "c", {"type": "interval", "seconds": 60}))
    assert added["name"] == "c"
    assert added["kind"] == "collect"
    scheduled = svc.schedule_list()
    assert "c" in scheduled
    removed = json.loads(svc.schedule_remove("c"))
    assert removed["removed"] is True
    assert "c" not in svc.schedule_list()


async def test_schedule_run_executes_reminder() -> None:
    svc = make_service()
    svc.schedule_add(
        "reminder", "r", {"type": "at", "at": "2026-09-25T00:00:00Z"}, {"message": "напомни"}
    )
    result = json.loads(await svc.schedule_run("r"))
    assert result["summary"]["note"] == "напомни"


async def test_data_append_and_query_aggregate() -> None:
    svc = make_service()
    svc.data_append("m", {"value": 5})
    svc.data_append("m", {"value": 7})
    assert json.loads(svc.data_query("m", op="count")) == {"count": 2}
    assert json.loads(svc.data_query("m", op="sum")) == {"count": 2, "value": 12}
    assert json.loads(svc.data_query("m", op="avg")) == {"count": 2, "value": 6.0}
    assert json.loads(svc.data_query("m", op="min")) == {"count": 2, "value": 5}
    assert json.loads(svc.data_query("m", op="max")) == {"count": 2, "value": 7}
    assert json.loads(svc.data_query("m", op="count", since="2999-01-01T00:00:00+00:00")) is None


def test_summary_report_empty_then_after_run() -> None:
    svc = make_service()
    svc.schedule_add("summary", "s", {"type": "interval", "seconds": 60}, {"data_kind": "m"})
    assert json.loads(svc.summary_report("s")) == {}
    svc.data_append("m", {"value": 2})
    json.loads(run_async(svc.schedule_run("s")))
    report = json.loads(svc.summary_report("s"))
    assert report["summary"]["aggregate"]["count"] == 1


async def test_make_server_lists_schedule_tools() -> None:
    server = make_server(":memory:")
    tools = await server.list_tools()
    names = sorted(t.name for t in tools)
    assert names == [
        "data_append",
        "data_query",
        "schedule_add",
        "schedule_list",
        "schedule_remove",
        "schedule_run",
        "summary_report",
    ]


def test_schedule_add_interval_once_sets_next_run() -> None:
    svc = make_service()
    added = json.loads(
        svc.schedule_add(
            "reminder", "r", {"type": "interval", "seconds": 300, "repeat": False}, {"m": "x"}
        )
    )
    assert added["trigger"]["repeat"] is False
    assert added["next_run"] is not None


async def test_make_server_call_schedule_add_round_trip() -> None:
    server = make_server(":memory:")
    result = await server.call_tool(
        "schedule_add",
        {"kind": "collect", "name": "c", "trigger": {"type": "interval", "seconds": 30}},
    )
    assert result.is_error is False
    text = _tool_text(result)
    payload = json.loads(text)
    assert payload["name"] == "c"
    assert payload["next_run"] is not None
    # вызвали без `url`/`payload` — пустой payload
    assert payload["payload"] == {}


def _tool_text(result: object) -> str:
    content = getattr(result, "content", None)
    parts = [c.text for c in content if getattr(c, "type", "") == "text"]
    return "\n".join(parts)


def test_schedule_add_stores_owner() -> None:
    svc = make_service()
    added = json.loads(
        svc.schedule_add(
            "collect",
            "c",
            {"type": "interval", "seconds": 60},
            owner_agent_id="a1",
            owner_session_id="s1",
            owner_project_id="p1",
        )
    )
    assert added["owner_agent_id"] == "a1"
    assert added["owner_session_id"] == "s1"
    assert added["owner_project_id"] == "p1"


async def test_job_ran_notify_posts_webhook() -> None:
    from unittest.mock import patch

    store = SchedulerStore(Path(":memory:"))
    sched = Scheduler(store, tick=3600, notify_url="http://x/notify")
    store.add_job(
        "reminder", "r", {"type": "at", "at": "2026-09-25T00:00:00Z"}, {"message": "напомни"}
    )

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    with patch("mcp_scheduler.scheduler.httpx.AsyncClient", return_value=client):
        await sched.run_now("r")

    assert len(captured) == 1
    payload = json.loads(captured[0].content)
    assert payload["event"] == "job_ran"
    assert payload["job"]["name"] == "r"
    assert payload["summary"]["note"] == "напомни"
