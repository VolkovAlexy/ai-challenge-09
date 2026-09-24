"""Доставка результатов планировщика в сессию создателя (webhook /api/scheduler/notify)."""

from __future__ import annotations

from agent.core.message import Message, Role
from tests.test_web_server import build_world, create_agent, make_client


async def test_job_ran_delivers_to_live_agent() -> None:
    """Результат задания попадает в историю live-агента, unread растёт."""
    world = build_world()
    async with await make_client(world) as client:
        agent = await create_agent(client)
        agent_id = agent["id"]
        session_id = world.state.get(agent_id).session_id  # type: ignore[union-attr]

        resp = await client.post(
            "/api/scheduler/notify",
            json={
                "event": "job_ran",
                "job": {"kind": "reminder", "name": "напоминание", "owner_session_id": session_id},
                "summary": {"note": "поднять проект"},
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    record = world.state.get(agent_id)
    assert record is not None
    history = record.agent.memory.history
    assert history[-1].role is Role.ASSISTANT
    assert "поднять проект" in (history[-1].content or "")
    assert world.store.unread_count(session_id) == 1


async def test_job_ran_delivers_to_closed_session() -> None:
    """Если live-агента нет, результат пишется напрямую в хранилище сессии."""
    from agent.config.schema import AgentSettings

    world = build_world()
    settings = AgentSettings.from_config(world.state.config)
    sid = "sess-closed"
    world.store.snapshot(
        sid,
        "t",
        settings,
        "SP",
        [Message(role=Role.USER, content="привет")],
    )

    async with await make_client(world) as client:
        resp = await client.post(
            "/api/scheduler/notify",
            json={
                "event": "job_ran",
                "job": {"kind": "collect", "name": "сбор", "owner_session_id": sid},
                "summary": {"collected": True},
            },
        )
        assert resp.status_code == 200

    data = world.store.get(sid)
    assert data is not None
    assert data.history[-1].role is Role.ASSISTANT
    assert "собраны" in (data.history[-1].content or "")
    assert world.store.unread_count(sid) == 1


async def test_job_added_removed_flags_and_mark_read() -> None:
    """job_added/removed меняют has_scheduled, mark-read сбрасывает unread."""
    from agent.config.schema import AgentSettings

    world = build_world()
    settings = AgentSettings.from_config(world.state.config)
    sid = "sess-flags"
    world.store.snapshot(sid, "t", settings, "SP", [Message(role=Role.USER, content="привет")])

    async with await make_client(world) as client:
        await client.post(
            "/api/scheduler/notify",
            json={"event": "job_added", "job": {"owner_session_id": sid}},
        )
        sessions = (await client.get("/api/sessions")).json()
        row = next(s for s in sessions if s["id"] == sid)
        assert row["has_scheduled"] is True

        await client.post(
            "/api/scheduler/notify",
            json={
                "event": "job_ran",
                "job": {"kind": "reminder", "name": "r", "owner_session_id": sid},
                "summary": {"note": "пора"},
            },
        )
        sessions = (await client.get("/api/sessions")).json()
        row = next(s for s in sessions if s["id"] == sid)
        assert row["unread_notifications"] == 1

        await client.post("/api/scheduler/mark-read", params={"session_id": sid})
        sessions = (await client.get("/api/sessions")).json()
        row = next(s for s in sessions if s["id"] == sid)
        assert row["unread_notifications"] == 0

        await client.post(
            "/api/scheduler/notify",
            json={"event": "job_removed", "job": {"owner_session_id": sid}},
        )
        sessions = (await client.get("/api/sessions")).json()
        row = next(s for s in sessions if s["id"] == sid)
        assert row["has_scheduled"] is False


async def test_notify_without_owner_session_is_noop() -> None:
    """Событие без owner_session_id ничего не меняет."""
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.post(
            "/api/scheduler/notify",
            json={"event": "job_ran", "job": {"kind": "reminder", "name": "r"}, "summary": {}},
        )
        assert resp.status_code == 200
