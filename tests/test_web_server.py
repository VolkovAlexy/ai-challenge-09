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


async def test_commands_contains() -> None:
    world = build_world()
    async with await make_client(world) as client:
        resp = await client.get("/api/commands")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()]
        assert "help" in names
        assert "model" in names
        assert "session" in names


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


