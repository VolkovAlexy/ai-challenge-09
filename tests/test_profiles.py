"""Интеграционные тесты сущности «Профиль»: CRUD + привязка к проекту + активный профиль чата."""

from __future__ import annotations

import httpx

from agent.core.message import ChatChunk
from agent.tools.registry import ToolRegistry
from agent.web_server.app import create_app
from agent.web_server.state import WebState
from tests.test_projects import create_agent_in, create_project
from tests.test_web_server import World, build_world, make_client, make_config, send_one


async def create_profile(client: httpx.AsyncClient, name: str, content: str) -> dict:
    resp = await client.post("/api/profiles", json={"name": name, "content": content})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def bind_profiles(
    client: httpx.AsyncClient, project_id: str, profile_ids: list[str]
) -> list[dict]:
    resp = await client.put(
        f"/api/projects/{project_id}/profiles", json={"profile_ids": profile_ids}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_profile_crud() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_profile(client, "Разработчик", "Ты — senior-разработчик.")
        assert body["id"]
        assert body["name"] == "Разработчик"
        assert body["content"] == "Ты — senior-разработчик."

        listed = (await client.get("/api/profiles")).json()
        assert any(p["id"] == body["id"] for p in listed)

        got = (await client.get(f"/api/profiles/{body['id']}")).json()
        assert got["name"] == "Разработчик"

        patched = await client.patch(
            f"/api/profiles/{body['id']}", json={"name": "Backend", "content": "Ты — бэкенд."}
        )
        assert patched.status_code == 200
        assert patched.json()["name"] == "Backend"

        resp = await client.delete(f"/api/profiles/{body['id']}")
        assert resp.status_code == 200
        assert (await client.get(f"/api/profiles/{body['id']}")).status_code == 404


async def test_create_profile_empty_name_400() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.post("/api/profiles", json={"name": "   ", "content": "x"})
        assert resp.status_code == 400


async def test_project_profiles_binding() -> None:
    world = build_world()
    async with await make_client(world) as client:
        p1 = await create_profile(client, "P1", "текст 1")
        p2 = await create_profile(client, "P2", "текст 2")
        project = await create_project(client, "Работа")
        bound = await bind_profiles(client, project["id"], [p1["id"], p2["id"]])
        assert [b["id"] for b in bound] == [p1["id"], p2["id"]]

        got = (await client.get(f"/api/projects/{project['id']}/profiles")).json()
        assert [g["id"] for g in got] == [p1["id"], p2["id"]]

        default = (await client.get("/api/projects")).json()
        d = next(x for x in default if x["id"] == "default")
        assert d["profile_ids"] == []


async def test_bind_unknown_profile_400() -> None:
    world = build_world()
    async with await make_client(world) as client:
        project = await create_project(client, "Работа")
        resp = await client.put(
            f"/api/projects/{project['id']}/profiles", json={"profile_ids": ["nope"]}
        )
        assert resp.status_code == 400


async def test_set_active_profile_applies_to_prompt() -> None:
    world = build_world()
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior-разработчик.")
        project = await create_project(client, "Работа")
        await bind_profiles(client, project["id"], [profile["id"]])
        body = await create_agent_in(client, project["id"])

        resp = await client.patch(
            f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["active_profile_id"] == profile["id"]

        record = world.state.get(body["id"])
        assert record is not None
        assert record.agent.effective_system_prompt == "SP\n\nТы — senior-разработчик."


async def test_set_active_profile_not_in_project_400() -> None:
    world = build_world()
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior.")
        project = await create_project(client, "Работа")
        body = await create_agent_in(client, project["id"])
        resp = await client.patch(
            f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]}
        )
        assert resp.status_code == 400


async def test_clear_active_profile() -> None:
    world = build_world()
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior.")
        project = await create_project(client, "Работа")
        await bind_profiles(client, project["id"], [profile["id"]])
        body = await create_agent_in(client, project["id"])
        await client.patch(f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]})

        resp = await client.patch(
            f"/api/agents/{body['id']}", json={"active_profile_id": ""}
        )
        assert resp.status_code == 200
        assert resp.json()["active_profile_id"] == ""
        record = world.state.get(body["id"])
        assert record is not None
        assert record.agent.effective_system_prompt == "SP"


async def test_delete_profile_clears_agent_active() -> None:
    world = build_world()
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior.")
        project = await create_project(client, "Работа")
        await bind_profiles(client, project["id"], [profile["id"]])
        body = await create_agent_in(client, project["id"])
        await client.patch(f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]})

        resp = await client.delete(f"/api/profiles/{profile['id']}")
        assert resp.status_code == 200
        record = world.state.get(body["id"])
        assert record is not None
        assert record.agent.active_profile_id == ""


async def test_active_profile_survives_snapshot_and_load() -> None:
    world = build_world([ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05)
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior.")
        project = await create_project(client, "Работа")
        await bind_profiles(client, project["id"], [profile["id"]])
        body = await create_agent_in(client, project["id"])
        await client.patch(f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]})
        await send_one(client, body["id"], "привет")

    new_state = WebState(
        config=make_config(),  # type: ignore[arg-type]
        llm=world.state.llm,
        tools=ToolRegistry(),
        store=world.store,
        default_system_prompt="SP",
    )
    second_world = World(state=new_state, app=create_app(new_state), store=world.store)
    async with await make_client(second_world) as client:
        sessions = (await client.get("/api/sessions")).json()
        assert len(sessions) == 1
        body = await create_agent_in(client, sessions[0]["project_id"])
        resp = await client.post(
            f"/api/agents/{body['id']}/load-session", json={"session_id": sessions[0]["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["active_profile_id"] == profile["id"]
        record = new_state.get(body["id"])
        assert record is not None
        assert record.agent.effective_system_prompt == "SP\n\nТы — senior."


async def test_branch_carries_active_profile() -> None:
    world = build_world([ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05)
    async with await make_client(world) as client:
        profile = await create_profile(client, "Разработчик", "Ты — senior.")
        project = await create_project(client, "Работа")
        await bind_profiles(client, project["id"], [profile["id"]])
        body = await create_agent_in(client, project["id"])
        await client.patch(f"/api/agents/{body['id']}", json={"active_profile_id": profile["id"]})
        await send_one(client, body["id"], "привет")

        sessions = (await client.get("/api/sessions", params={"project_id": project["id"]})).json()
        assert len(sessions) == 1
        resp = await client.post(f"/api/sessions/{sessions[0]['id']}/branch")
        assert resp.status_code == 200
        assert resp.json()["active_profile_id"] == profile["id"]
