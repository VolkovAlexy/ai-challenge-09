"""Интеграционные тесты web-бэкенда (FastAPI + httpx ASGI-клиент).

LLM замокан (MockLLM), SSE-стриминг проверяется на реальном EventSourceResponse.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI

from agent.config.schema import validate_config
from agent.core.message import ChatChunk, ToolCallDelta
from agent.memory.persistence import SessionStore
from agent.tools.registry import ToolRegistry
from agent.web_server.app import create_app
from agent.web_server.state import WebState
from agent.web_server.stream import agent_stream


class MockLLM:
    """Мок LLMClient: отдаёт заданные чанки с заданной задержкой."""

    def __init__(self, chunks: list[ChatChunk], delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = delay

    async def astream(
        self, request: object, api_base: str, api_key: str
    ) -> AsyncIterator[ChatChunk]:
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def close(self) -> None:
        return None


class SeqMockLLM:
    """Мок LLM: отдаёт отдельную последовательность чанков на каждый вызов."""

    def __init__(self, sequences: list[list[ChatChunk]]) -> None:
        self.sequences = [list(seq) for seq in sequences]
        self.calls = 0

    async def astream(
        self, request: object, api_base: str, api_key: str
    ) -> AsyncIterator[ChatChunk]:
        self.calls += 1
        sequence = self.sequences.pop(0) if self.sequences else [ChatChunk(content="")]
        for chunk in sequence:
            yield chunk

    async def close(self) -> None:
        return None


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": {"m1": 32768}},
            },
            "default_model": "p1:m1",
        }
    )


@dataclass
class World:
    state: WebState
    app: FastAPI
    store: SessionStore


def build_world(
    chunks: list[ChatChunk] | None = None,
    delay: float = 0.0,
    path: Path | None = None,
) -> World:
    llm = MockLLM(chunks or [ChatChunk(content="ok")], delay=delay)
    store = SessionStore(path or Path(":memory:"))
    state = WebState(
        config=make_config(),
        llm=llm,  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=store,
        default_system_prompt="SP",
    )
    return World(state=state, app=create_app(state), store=store)


async def make_client(world: World) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=world.app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def collect_sse(response: httpx.Response) -> list[tuple[str, dict]]:
    """Читает SSE-поток: каждая data-строка → (event, payload)."""
    events: list[tuple[str, dict]] = []
    async for line in response.aiter_lines():
        line = line.strip()
        if line.startswith("data:"):
            payload = json.loads(line[len("data:") :].strip())
            events.append((payload.pop("event"), payload))
    return events


async def create_agent(client: httpx.AsyncClient) -> dict:
    resp = await client.post("/api/agents", json={"name": "test"})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_config_no_api_key() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        assert "api_key" not in resp.text
        body = resp.json()
        assert body["default_model"] == "p1:m1"


async def test_knowledge_disabled_default() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.get("/api/knowledge")
        assert resp.status_code == 200
        assert "api_key" not in resp.text
        body = resp.json()
        assert body["enabled"] is False
        assert body["embedding_model"] is None
        assert body["chunk_strategy"] == "paragraph"
        assert body["chunk_size"] == 512
        assert body["chunk_overlap"] == 64
        assert body["top_k"] == 4
        assert body["retrieve_top_k"] == 20
        assert body["relevance_enabled"] is False
        assert body["relevance_threshold"] == 0.6
        assert body["indexing"] is False
        assert body["size"] == 0


async def test_knowledge_patch_without_knowledge_returns_disabled() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.post("/api/knowledge", json={"enabled": True})
        assert resp.status_code == 200
        assert "api_key" not in resp.text
        assert resp.json()["enabled"] is False
        assert resp.json()["embedding_model"] is None


async def test_commands_contains() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.get("/api/commands")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "close" in names
        assert "export" in names
        assert "help" not in names
        assert "model" not in names
        assert "session" not in names


async def test_patch_context_settings() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        rid = body["id"]
        resp = await client.patch(
            f"/api/agents/{rid}",
            json={
                "context_strategy": "sliding",
                "sliding_window": 12,
                "temperature": 0.3,
                "compaction_threshold": 0.7,
            },
        )
        assert resp.status_code == 200, resp.text
        settings = resp.json()["settings"]
        assert settings["context_strategy"] == "sliding"
        assert settings["sliding_window"] == 12
        assert settings["temperature"] == 0.3
        assert settings["compaction_threshold"] == 0.7


async def test_patch_compaction_threshold_invalid() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        for value in (0.4, 1.1):
            resp = await client.patch(
                f"/api/agents/{body['id']}",
                json={"context_strategy": "summary", "compaction_threshold": value},
            )
            assert resp.status_code >= 400, resp.text


async def test_patch_context_strategy_invalid() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        resp = await client.patch(
            f"/api/agents/{body['id']}", json={"context_strategy": "bogus"}
        )
        assert resp.status_code >= 400


async def test_facts_roundtrip() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        rid = body["id"]
        put = await client.put(f"/api/agents/{rid}/facts", json={"facts": {"a": "b"}})
        assert put.status_code == 200, put.text
        assert put.json() == {"a": "b"}
        get = await client.get(f"/api/agents/{rid}/facts")
        assert get.status_code == 200
        assert get.json() == {"a": "b"}


async def test_create_agent_returns_dto() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        assert body["id"]
        assert body["name"] == "test"
        assert body["model"] == "p1:m1"
        assert body["streaming"] is False
        assert body["invariants"] == []


async def test_patch_unknown_model_400() -> None:
    world = build_world()
    async with await make_client(world) as client:
        body = await create_agent(client)
        resp = await client.patch(f"/api/agents/{body['id']}", json={"model": "p1:nope"})
        assert resp.status_code == 400
        assert "detail" in resp.json()


async def test_delete_nonexistent_404() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.delete("/api/agents/nope")
        assert resp.status_code == 404


async def test_post_message_during_stream_409() -> None:
    world = build_world(
        [ChatChunk(content="не спешим"), ChatChunk(content=""), ChatChunk(finish_reason="stop")],
        delay=0.3,
    )
    agent = world.state.create_agent()
    task = agent.agent.start_ask("длинный запрос")
    async with await make_client(world) as client:
        resp = await client.post(f"/api/agents/{agent.agent_id}/messages", json={"content": "ещё"})
        assert resp.status_code == 409
    await task


async def test_sse_stream_deltas_to_done() -> None:
    world = build_world(
        [ChatChunk(content="Привет"), ChatChunk(content=" мир"), ChatChunk(finish_reason="stop")],
        delay=0.05,
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "hi"}
        ) as resp:
            assert resp.status_code == 200
            events = await collect_sse(resp)
    assert events[0][0] == "user_message"
    assert events[0][1]["message"]["content"] == "hi"
    assert events[0][1]["message"]["role"] == "user"
    deltas = "".join(payload["content"] for name, payload in events if name == "delta")
    done = [payload for name, payload in events if name == "done"]
    assert len(done) == 1
    assert deltas == done[0]["message"]["content"] == "Привет мир"
    assert done[0]["message"]["role"] == "assistant"


async def test_sse_cancelled_after_cancel() -> None:
    world = build_world(
        [ChatChunk(content="частичн"), ChatChunk(content="о"), ChatChunk(finish_reason="stop")],
        delay=0.3,
    )
    record = world.state.create_agent()
    events: list[dict] = []

    async def consume() -> None:
        async for event in agent_stream(world.state, record, "hi"):
            events.append(json.loads(event["data"]))

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    assert record.agent.cancel_ask() is True
    await consumer
    names = [payload["event"] for payload in events]
    assert "cancelled" in names
    assert "done" not in names


async def test_clear_messages_empties_history() -> None:
    world = build_world(
        [ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")],
        delay=0.05,
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "вопрос"}
        ) as resp:
            await collect_sse(resp)
        msgs = (await client.get(f"/api/agents/{agent_id}/messages")).json()
        assert len(msgs) == 2
        resp = await client.delete(f"/api/agents/{agent_id}/messages")
        assert resp.status_code == 200
        msgs_after = (await client.get(f"/api/agents/{agent_id}/messages")).json()
        assert msgs_after == []


async def test_restore_agents_with_history() -> None:
    world = build_world(
        [ChatChunk(content="со хранение"), ChatChunk(finish_reason="stop")],
        delay=0.05,
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "привет"}
        ) as resp:
            await collect_sse(resp)

    # пересоздаём состояние поверх того же store: при старте агенты НЕ восстанавливаются
    new_state = WebState(
        config=make_config(),
        llm=world.state.llm,
        tools=ToolRegistry(),
        store=world.store,
        default_system_prompt="SP",
    )
    second_world = World(
        state=new_state, app=create_app(new_state), store=world.store
    )
    async with await make_client(second_world) as client:
        # реестр пуст — вкладки не открываются
        assert (await client.get("/api/agents")).json() == []
        # сессия осталась в палитре
        sessions = (await client.get("/api/sessions")).json()
        assert len(sessions) == 1
        session_id = sessions[0]["id"]
        # пользователь сам создаёт агента и открывает нужную сессию
        body = await create_agent(client)
        agent_id = body["id"]
        resp = await client.post(
            f"/api/agents/{agent_id}/load-session", json={"session_id": session_id}
        )
        assert resp.status_code == 200
        msgs = (await client.get(f"/api/agents/{agent_id}/messages")).json()
        assert len(msgs) == 2
        assert msgs[0]["content"] == "привет"
        assert msgs[1]["content"] == "со хранение"


async def test_agent_dto_exposes_session_id() -> None:
    """Вкладка знает свою сессию: фронтенд по session_id связывает карточку и агента."""
    world = build_world(
        [ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        # у новой вкладки сразу есть своя сессия
        assert body["session_id"] != ""
        await send_one(client, agent_id, "вопрос")
        sessions = (await client.get("/api/sessions")).json()
        assert len(sessions) == 1
        dto = next(
            a for a in (await client.get("/api/agents")).json() if a["id"] == agent_id
        )
        assert dto["session_id"] == sessions[0]["id"]
        # load-session переключает запись на выбранную сессию — DTO это отражает
        other = await create_agent(client)
        resp = await client.post(
            f"/api/agents/{other['id']}/load-session", json={"session_id": sessions[0]["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["session_id"] == sessions[0]["id"]


async def send_one(client: httpx.AsyncClient, agent_id: str, content: str) -> None:
    async with client.stream(
        "POST", f"/api/agents/{agent_id}/messages", json={"content": content}
    ) as resp:
        assert resp.status_code == 200
        await collect_sse(resp)


async def test_delete_session() -> None:
    world = build_world(
        [ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        await send_one(client, body["id"], "вопрос")
        sessions = (await client.get("/api/sessions")).json()
        assert len(sessions) == 1
        resp = await client.delete(f"/api/sessions/{sessions[0]['id']}")
        assert resp.status_code == 200
        assert (await client.get("/api/sessions")).json() == []


async def test_delete_missing_session_404() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.delete("/api/sessions/nope")
        assert resp.status_code == 404


async def test_branch_session_creates_copy() -> None:
    world = build_world(
        [ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        await send_one(client, body["id"], "вопрос")
        sessions = (await client.get("/api/sessions")).json()
        assert len(sessions) == 1
        resp = await client.post(f"/api/sessions/{sessions[0]['id']}/branch")
        assert resp.status_code == 200
        branch = resp.json()
        # новая вкладка с историей исходной сессии
        msgs = (await client.get(f"/api/agents/{branch['id']}/messages")).json()
        assert [m["content"] for m in msgs] == ["вопрос", "ответ"]
        # исходная сессия не тронута: осталась в списке и её история не изменилась
        after = (await client.get("/api/sessions")).json()
        assert any(s["id"] == sessions[0]["id"] for s in after)
        assert [s["id"] for s in after].count(sessions[0]["id"]) == 1


async def test_branch_missing_session_404() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.post("/api/sessions/nope/branch")
        assert resp.status_code == 404


async def test_rename_session_sets_title() -> None:
    world = build_world(
        [ChatChunk(content="ответ"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        await send_one(client, body["id"], "вопрос")
        session_id = (await client.get("/api/sessions")).json()[0]["id"]
        resp = await client.patch(
            f"/api/sessions/{session_id}", json={"title": "Мой заголовок"}
        )
        assert resp.status_code == 200
        sessions = (await client.get("/api/sessions")).json()
        assert sessions[0]["title"] == "Мой заголовок"


async def _create_sessions(client: httpx.AsyncClient, n: int) -> list[str]:
    """Создаёт n сессий (по агенту + сообщению) и возвращает их id."""
    created = []
    for i in range(n):
        body = await create_agent(client)
        await send_one(client, body["id"], f"вопрос-{i}")
    sessions = (await client.get("/api/sessions")).json()
    created = [s["id"] for s in sessions]
    assert len(created) == n
    return created


async def test_batch_delete_sessions() -> None:
    world = build_world(
        [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        ids = await _create_sessions(client, 2)
        resp = await client.post("/api/sessions/batch/delete", json={"ids": ids})
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 2
        assert (await client.get("/api/sessions")).json() == []


async def test_batch_delete_unknown_mixed() -> None:
    world = build_world(
        [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        ids = await _create_sessions(client, 1)
        resp = await client.post(
            "/api/sessions/batch/delete", json={"ids": [ids[0], "nope"]}
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] == 1


async def test_batch_move_sessions() -> None:
    world = build_world(
        [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        ids = await _create_sessions(client, 2)
        assert all(s["project_id"] == "default" for s in (await client.get("/api/sessions")).json())
        proj = (await client.post("/api/projects", json={"name": "Проект 2"})).json()
        resp = await client.post(
            "/api/sessions/batch/move", json={"ids": ids, "project_id": proj["id"]}
        )
        assert resp.status_code == 200
        assert resp.json()["moved"] == 2
        after = (await client.get("/api/sessions")).json()
        assert all(s["project_id"] == proj["id"] for s in after)


async def test_batch_move_missing_project_404() -> None:
    world = build_world(
        [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")], delay=0.05
    )
    async with await make_client(world) as client:
        ids = await _create_sessions(client, 1)
        resp = await client.post(
            "/api/sessions/batch/move", json={"ids": ids, "project_id": "nope"}
        )
        assert resp.status_code == 404


async def test_batch_export_writes_jsonl(tmp_path: Path) -> None:
    from agent.web_server import app as app_module

    original = app_module.SESSIONS_DIR
    app_module.SESSIONS_DIR = tmp_path
    try:
        world = build_world(
            [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")], delay=0.05
        )
        async with await make_client(world) as client:
            ids = await _create_sessions(client, 2)
            resp = await client.post("/api/sessions/batch/export", json={"ids": ids})
            assert resp.status_code == 200
            paths = resp.json()["paths"]
            assert len(paths) == 2
            for p in paths:
                assert Path(p).exists()
                assert Path(p).parent == tmp_path
    finally:
        app_module.SESSIONS_DIR = original


async def test_sse_streams_reasoning_delta_to_done() -> None:
    world = build_world(
        [
            ChatChunk(reasoning="думаю"),
            ChatChunk(reasoning=" ещё"),
            ChatChunk(content="ответ"),
            ChatChunk(finish_reason="stop"),
        ],
        delay=0.05,
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "вопрос"}
        ) as resp:
            assert resp.status_code == 200
            events = await collect_sse(resp)
    reasoning = "".join(
        payload["content"] for name, payload in events if name == "reasoning_delta"
    )
    deltas = "".join(payload["content"] for name, payload in events if name == "delta")
    done = [payload for name, payload in events if name == "done"]
    assert len(done) == 1
    # размышления уходят в стрим отдельным событием и в done-сообщении
    assert reasoning == done[0]["message"]["reasoning"] == "думаю ещё"
    assert deltas == done[0]["message"]["content"] == "ответ"


async def test_history_messages_include_reasoning() -> None:
    world = build_world(
        [ChatChunk(reasoning="шаг 1"), ChatChunk(content="отв"), ChatChunk(finish_reason="stop")],
        delay=0.05,
    )
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "вопрос"}
        ) as resp:
            await collect_sse(resp)
        msgs = (await client.get(f"/api/agents/{agent_id}/messages")).json()
        assert msgs[0]["reasoning"] is None  # user-сообщение без размышлений
        assert msgs[1]["reasoning"] == "шаг 1"


async def test_task_start_returns_planning_dto() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        resp = await client.post(
            f"/api/agents/{aid}/task",
            json={"operation": "start", "description": "написать модуль", "steps": ["a", "b"]},
        )
        assert resp.status_code == 200
        dto = resp.json()
        assert dto["task"] is not None
        assert dto["task"]["phase"] == "planning"
        assert dto["task"]["steps"] == ["a", "b"]


async def test_task_set_phase_invalid_transition_409() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        resp = await client.post(
            f"/api/agents/{aid}/task", json={"operation": "set_phase", "phase": "execution"}
        )
        assert resp.status_code == 409
        assert "detail" in resp.json()


async def test_task_unknown_agent_404() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.post("/api/agents/nope/task", json={"operation": "pause"})
        assert resp.status_code == 404


async def test_task_pause_and_resume() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        await client.post(
            f"/api/agents/{aid}/task", json={"operation": "start", "description": "x"}
        )
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "pause"})
        assert resp.status_code == 200
        assert resp.json()["task"]["paused"] is True
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "resume"})
        assert resp.json()["task"]["paused"] is False


async def test_task_reset_returns_no_task() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        await client.post(
            f"/api/agents/{aid}/task", json={"operation": "start", "description": "x"}
        )
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "reset"})
        assert resp.status_code == 200
        assert resp.json()["task"] is None


async def test_task_confirm_plan_transitions_to_execution() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        await client.post(
            f"/api/agents/{aid}/task",
            json={"operation": "start", "description": "x", "steps": ["а", "б"]},
        )
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "confirm_plan"})
        assert resp.status_code == 200
        task = resp.json()["task"]
        assert task["phase"] == "execution"
        assert task["plan_confirmed"] is True


async def test_task_confirm_plan_requires_planning() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        # нет активной задачи — из planning подтверждать нельзя
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "confirm_plan"})
        assert resp.status_code == 400


async def test_task_confirm_plan_requires_steps() -> None:
    world = build_world()
    async with await make_client(world) as client:
        aid = (await create_agent(client))["id"]
        await client.post(
            f"/api/agents/{aid}/task", json={"operation": "start", "description": "x"}
        )
        resp = await client.post(f"/api/agents/{aid}/task", json={"operation": "confirm_plan"})
        assert resp.status_code == 400


async def test_sse_streams_subagent_events() -> None:
    store = SessionStore(Path(":memory:"))
    llm = SeqMockLLM(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        ToolCallDelta(
                            index=0,
                            id="c1",
                            function_name="delegate",
                            function_arguments='{"role":"писатель","task":"напиши"}',
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="Драфт готов"), ChatChunk(finish_reason="stop")],
            [ChatChunk(content="Интегрирую."), ChatChunk(finish_reason="stop")],
        ]
    )
    state = WebState(
        config=make_config(),
        llm=llm,  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=store,
        default_system_prompt="SP",
    )
    world = World(state=state, app=create_app(state), store=store)
    profile = store.create_profile("писатель", "Ты писатель")
    store.set_project_profiles("default", [profile.id])
    async with await make_client(world) as client:
        body = await create_agent(client)
        agent_id = body["id"]
        await client.post(
            f"/api/agents/{agent_id}/task",
            json={"operation": "start", "description": "x", "steps": ["шаг"]},
        )
        await client.post(f"/api/agents/{agent_id}/task", json={"operation": "confirm_plan"})
        async with client.stream(
            "POST", f"/api/agents/{agent_id}/messages", json={"content": "поручи"}
        ) as resp:
            assert resp.status_code == 200
            events = await collect_sse(resp)
    names = [name for name, _ in events]
    assert "subagent_started" in names
    assert "subagent_done" in names
    deltas = "".join(payload["content"] for name, payload in events if name == "subagent_delta")
    assert deltas == "Драфт готов"


