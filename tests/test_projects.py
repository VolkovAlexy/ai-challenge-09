"""Интеграционные тесты сущности «Проект»: CRUD + изоляция долгосрочной памяти."""

from __future__ import annotations

import httpx

from agent.core.message import ChatChunk
from tests.test_web_server import build_world, make_client, send_one


async def create_project(client: httpx.AsyncClient, name: str) -> dict:
    resp = await client.post("/api/projects", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_agent_in(client: httpx.AsyncClient, project_id: str) -> dict:
    resp = await client.post("/api/agents", json={"name": "a", "project_id": project_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_projects_list_has_default() -> None:
    world = build_world()
    async with await make_client(world) as client:
        projects = (await client.get("/api/projects")).json()
        assert any(p["id"] == "default" for p in projects)


async def test_create_project_returns_dto() -> None:
    world = build_world()
    async with await make_client(world) as client:
        project = await create_project(client, "Работа")
        assert project["id"]
        assert project["name"] == "Работа"
        assert project["session_count"] == 0


async def test_agent_binds_to_project_and_session_grouped() -> None:
    world = build_world([ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05)
    async with await make_client(world) as client:
        project = await create_project(client, "Работа")
        body = await create_agent_in(client, project["id"])
        assert body["project_id"] == project["id"]

        await send_one(client, body["id"], "вопрос")
        sessions = (await client.get("/api/sessions", params={"project_id": project["id"]})).json()
        assert len(sessions) == 1
        assert sessions[0]["project_id"] == project["id"]


async def test_longterm_isolated_by_project() -> None:
    world = build_world()
    async with await make_client(world) as client:
        p1 = await create_project(client, "П1")
        p2 = await create_project(client, "П2")
        await client.post(
            "/api/longterm", params={"project_id": p1["id"]}, json={"content": "знание 1"}
        )
        await client.post(
            "/api/longterm", params={"project_id": p2["id"]}, json={"content": "знание 2"}
        )

        lt1 = (await client.get("/api/longterm", params={"project_id": p1["id"]})).json()
        lt2 = (await client.get("/api/longterm", params={"project_id": p2["id"]})).json()
        assert lt1["entries"] == ["знание 1"]
        assert lt2["entries"] == ["знание 2"]
        assert lt1["entries"] != lt2["entries"]


async def test_delete_default_project_400() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.delete("/api/projects/default")
        assert resp.status_code == 400


async def test_delete_project_removes_sessions() -> None:
    world = build_world([ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05)
    async with await make_client(world) as client:
        project = await create_project(client, "Работа")
        body = await create_agent_in(client, project["id"])
        await send_one(client, body["id"], "вопрос")
        sessions = await client.get("/api/sessions", params={"project_id": project["id"]})
        assert len(sessions.json()) == 1

        resp = await client.delete(f"/api/projects/{project['id']}")
        assert resp.status_code == 200
        sessions = (await client.get("/api/sessions")).json()
        assert all(s["project_id"] != project["id"] for s in sessions)


async def test_rename_project() -> None:
    world = build_world()
    async with await make_client(world) as client:
        project = await create_project(client, "Старое")
        resp = await client.patch(f"/api/projects/{project['id']}", json={"name": "Новое"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Новое"
