"""Трёхуровневая память: долгосрочная (проект в БД), рабочая (scratchpad+tools), предложения.

Покрывает: инжект долгосрочной памяти в проекцию,
исполнение tool_calls (scratchpad-инструменты), вырезание
[MEMORY_SUGGESTION], fork_at, REST-эндпоинты /longterm, /fork,
/scratchpad, memory-suggestion.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from agent.config.schema import AgentSettings, validate_config
from agent.core.agent import Agent
from agent.core.context import INVARIANTS_HEADER
from agent.core.message import ChatChunk, Message, Role
from agent.memory.longterm import LongTermSource, ProjectLongTermMemory
from agent.memory.persistence import SessionStore
from agent.tools.registry import ToolRegistry
from agent.web_server.app import create_app
from agent.web_server.state import WebState


class MockLLM:
    """Мок LLMClient: отдаёт заданные чанки, запоминает параметры запроса."""

    def __init__(self, chunks: list[ChatChunk], delay: float = 0.0) -> None:
        self.chunks = list(chunks)
        self.delay = delay
        self.calls: list[object] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append(request)
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
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


def make_agent(
    chunks: list[ChatChunk],
    longterm: LongTermSource | None = None,
) -> tuple[Agent, MockLLM]:
    llm = MockLLM(chunks)
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),  # type: ignore[arg-type]
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
        longterm=longterm,
    )
    return agent, llm


# --- долговременная память (проект в БД) ---


def test_projection_contains_longterm_and_scratchpad() -> None:
    store = SessionStore(":memory:")
    store.ensure_project("default", "По умолчанию")
    longterm = ProjectLongTermMemory(store, "default")
    longterm.append("пользователь предпочитает python")
    agent, _ = make_agent([ChatChunk(content="ok")], longterm=longterm)
    agent.memory.scratchpad = "задача: починить тесты"
    messages = agent._projection()
    contents = [m.content or "" for m in messages]
    assert any("пользователь предпочитает python" in c for c in contents)
    assert any("задача: починить тесты" in c for c in contents)


def test_projection_without_longterm(tmp_path) -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    messages = agent._projection()
    assert [m.role for m in messages] == [Role.SYSTEM]


def test_projection_contains_invariants() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    agent.memory.invariants = ["не удалять лог", "использовать только python"]
    messages = agent._projection()
    contents = "\n".join(m.content or "" for m in messages)
    assert INVARIANTS_HEADER in contents
    assert "- не удалять лог" in contents
    assert "- использовать только python" in contents


def test_projection_deepcopies_invariants() -> None:
    """Инварианты не мутируют через проекцию (копия списка)."""
    agent, _ = make_agent([ChatChunk(content="ok")])
    agent.memory.invariants = ["a"]
    before = agent.memory.invariants
    agent._projection()
    assert agent.memory.invariants == before


# --- MEMORY_SUGGESTION ---


def test_memory_suggestion_extracted_from_answer() -> None:
    chunks = [
        ChatChunk(content="Отвечаю. [MEMORY_SUGGESTION]Пользователь в Москве[/MEMORY_SUGGESTION]"),
        ChatChunk(finish_reason="stop"),
    ]
    agent, _ = make_agent(chunks)
    answer = asyncio.run(agent.ask("hi"))
    assert answer == "Отвечаю."
    assert "[MEMORY_SUGGESTION]" not in (agent.memory.history[-1].content or "")
    assert agent.pending_memory_suggestion == "Пользователь в Москве"


def test_memory_suggestion_absent_leaves_answer() -> None:
    agent, _ = make_agent([ChatChunk(content="обычный ответ")])
    answer = asyncio.run(agent.ask("hi"))
    assert answer == "обычный ответ"
    assert agent.pending_memory_suggestion is None


def test_dismiss_suggestion() -> None:
    agent, _ = make_agent(
        [ChatChunk(content="[MEMORY_SUGGESTION]знание[/MEMORY_SUGGESTION]")]
    )
    asyncio.run(agent.ask("hi"))
    assert agent.pending_memory_suggestion == "знание"
    agent.dismiss_suggestion()
    assert agent.pending_memory_suggestion is None


# --- рабочая память: инструменты scratchpad ---


def _tcd(*, index: int, id: str, name: str, args: str) -> object:
    """ToolCallDelta для чанка стрима."""
    from agent.core.message import ToolCallDelta

    return ToolCallDelta(index=index, id=id, function_name=name, function_arguments=args)


class RoundLLM:
    """Мок LLM: каждый astream-вызов получает следующий список чанков (раунды tools)."""

    def __init__(self, rounds: list[list[ChatChunk]]) -> None:
        self.rounds = list(rounds)
        self.seen: list[list[object]] = []

    async def astream(
        self, request: object, api_base: str, api_key: str
    ) -> AsyncIterator[ChatChunk]:
        self.seen.append(request)
        idx = min(len(self.seen) - 1, len(self.rounds) - 1)
        for chunk in self.rounds[idx]:
            yield chunk

    async def close(self) -> None:
        return None


def make_round_agent(rounds: list[list[ChatChunk]]) -> tuple[Agent, RoundLLM]:
    llm = RoundLLM(rounds)
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),  # type: ignore[arg-type]
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    return agent, llm


def test_write_scratchpad_via_tool_loop() -> None:
    agent, llm = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(
                            index=0,
                            id="c1",
                            name="write_scratchpad",
                            args='{"text": "план: 1) тесты"}',
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="готово"), ChatChunk(finish_reason="stop")],
        ]
    )
    answer = asyncio.run(agent.ask("запиши план"))
    assert answer == "готово"
    assert agent.memory.scratchpad == "план: 1) тесты"
    roles = [m.role for m in agent.memory.history]
    assert roles == [Role.USER, Role.ASSISTANT, Role.TOOL, Role.ASSISTANT]
    tool_msg = agent.memory.history[2]
    assert tool_msg.role is Role.TOOL
    assert "Рабочая память обновлена" in (tool_msg.content or "")
    # инструменты переданы в запрос к LLM: 3 scratchpad + 9 task + 4 invariants
    tools = getattr(llm.seen[0], "tools", None)
    assert tools is not None and len(tools) == 16


def test_append_and_read_scratchpad_tools() -> None:
    agent, llm = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="a1", name="append_scratchpad", args='{"text": "шаг 1"}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [
                ChatChunk(
                    tool_call_deltas=[_tcd(index=0, id="a2", name="read_scratchpad", args="{}")]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ok"), ChatChunk(finish_reason="stop")],
        ]
    )
    asyncio.run(agent.ask("работай"))

    assert agent.memory.scratchpad == "шаг 1"
    # в третьем запросе есть tool-сообщение с содержимым scratchpad
    third = getattr(llm.seen[2], "messages", [])
    tool_msgs = [m for m in third if m.role is Role.TOOL]
    assert any("шаг 1" in (m.content or "") for m in tool_msgs)


def test_invariant_add_remove_list_tools() -> None:
    """Подряд два раунда инструментов: invariant_add, затем invariant_list."""
    agent, llm = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(
                            index=0,
                            id="i1",
                            name="invariant_add",
                            args='{"text": "не удалять лог"}',
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="i2", name="invariant_list", args="{}")
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ясно"), ChatChunk(finish_reason="stop")],
        ]
    )
    answer = asyncio.run(agent.ask("запомни"))
    assert answer == "ясно"
    assert agent.memory.invariants == ["не удалять лог"]
    # инструменты переданы в запрос: 3 scratchpad + 9 task + 4 invariants
    tools = getattr(llm.seen[0], "tools", None)
    assert tools is not None and len(tools) == 16
    # во втором запросе инвариант инжектируется в системные сообщения
    second = getattr(llm.seen[1], "messages", [])
    system_blocks = "\n".join(m.content or "" for m in second if m.role is Role.SYSTEM)
    assert INVARIANTS_HEADER in system_blocks
    assert "- не удалять лог" in system_blocks
    # удаление
    agent2, _ = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="i3", name="invariant_add", args='{"text": "x"}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="i4", name="invariant_remove", args='{"index": 1}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")],
        ]
    )
    asyncio.run(agent2.ask("очисти"))
    assert agent2.memory.invariants == []


def test_invariant_remove_out_of_range_returns_error() -> None:
    agent, _ = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="i5", name="invariant_remove", args='{"index": 9}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ок"), ChatChunk(finish_reason="stop")],
        ]
    )
    asyncio.run(agent.ask("удали"))
    tool_msg = agent.memory.history[2]
    assert tool_msg.role is Role.TOOL
    assert "ограничения с номером" in (tool_msg.content or "")


def test_unknown_tool_returns_error_to_model() -> None:
    agent, _ = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[_tcd(index=0, id="x1", name="no_such_tool", args="{}")]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ok"), ChatChunk(finish_reason="stop")],
        ]
    )
    asyncio.run(agent.ask("hi"))
    tool_msg = agent.memory.history[2]
    assert tool_msg.role is Role.TOOL
    assert "не найден" in (tool_msg.content or "")


def test_turn_events_collected_during_tool_round() -> None:
    """Артефакты tool-раунда попадают в turn_events с верными индексами истории."""
    agent, _ = make_round_agent(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(
                            index=0,
                            id="c1",
                            name="write_scratchpad",
                            args='{"text": "план: 1) тесты"}',
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="готово"), ChatChunk(finish_reason="stop")],
        ]
    )
    asyncio.run(agent.ask("запиши план"))
    assert [(idx, msg.role) for idx, msg in agent.turn_events] == [
        (1, Role.ASSISTANT),
        (2, Role.TOOL),
    ]
    for idx, msg in agent.turn_events:
        assert agent.memory.history[idx] is msg


def test_message_dto_tool_fields() -> None:
    """message_dto отдаёт имя инструмента и имена вызванных tool_calls."""
    from agent.core.message import FunctionCall, ToolCall

    tool_msg = Message(
        role=Role.TOOL,
        content="Рабочая память обновлена",
        tool_call_id="c1",
        name="write_scratchpad",
    )
    dto = WebState.message_dto(tool_msg, 2)
    assert dto.tool_name == "write_scratchpad"
    assert dto.tool_calls is None

    asst_msg = Message(
        role=Role.ASSISTANT,
        content="записываю",
        tool_calls=[
            ToolCall(id="c1", function=FunctionCall(name="write_scratchpad", arguments="{}"))
        ],
    )
    dto = WebState.message_dto(asst_msg, 1)
    assert dto.tool_calls == ["write_scratchpad"]
    assert dto.tool_name is None


# --- ветвление от сообщения ---


def test_fork_at_truncates_history() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    asyncio.run(agent.ask("1"))
    asyncio.run(agent.ask("2"))
    assert len(agent.memory.history) == 4
    previous = agent.fork_at("branch-1", 1)
    assert previous == "main"
    # новая ветка: user + assistant первого хода
    assert len(agent.memory.history) == 2
    assert agent.memory.active_branch == "branch-1"
    # прежняя ветка цела
    agent.switch_branch("main")
    assert len(agent.memory.history) == 4


def test_fork_at_invalid_index() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    try:
        agent.fork_at("b", 5)
    except IndexError:
        pass
    else:
        raise AssertionError("ожидали IndexError")


# --- web-эндпоинты памяти ---


def build_state(tmp_path, chunks: list[ChatChunk] | None = None, llm: object | None = None):

    llm = llm or MockLLM(chunks or [ChatChunk(content="ok")])
    store = SessionStore(":memory:")
    state = WebState(
        config=make_config(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=store,
        default_system_prompt="SP",
    )
    return state, create_app(state)


async def client_for(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://test")


def test_web_longterm_remember_and_forget(tmp_path) -> None:
    _state, app = build_state(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            resp = await client.post("/api/longterm", json={"content": "знание 1"})
            assert resp.status_code == 200
            assert resp.json()["entries"] == ["знание 1"]
            resp = await client.post("/api/longterm", json={"content": "знание 2"})
            assert resp.json()["entries"] == ["знание 1", "знание 2"]
            resp = await client.delete("/api/longterm/0")
            assert resp.status_code == 200
            assert resp.json()["entries"] == ["знание 2"]
            resp = await client.delete("/api/longterm/99")
            assert resp.status_code == 404
            resp = await client.post("/api/longterm", json={"content": "   "})
            assert resp.status_code == 400

    asyncio.run(run())


def test_web_longterm_update(tmp_path) -> None:
    _state, app = build_state(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            await client.post("/api/longterm", json={"content": "зовут Демерзель"})
            resp = await client.put("/api/longterm/0", json={"content": "зовут Босс"})
            assert resp.status_code == 200
            assert resp.json()["entries"] == ["зовут Босс"]
            resp = await client.put("/api/longterm/99", json={"content": "любое"})
            assert resp.status_code == 404
            resp = await client.put("/api/longterm/0", json={"content": "  "})
            assert resp.status_code == 400

    asyncio.run(run())


def test_web_fork_and_suggestion(tmp_path) -> None:
    _state, app = build_state(
        tmp_path,
        chunks=[ChatChunk(content="ответ [MEMORY_SUGGESTION]паттерн X[/MEMORY_SUGGESTION]")],
    )

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            agent = (await client.post("/api/agents", json={"name": "t"})).json()
            agent_id = agent["id"]
            # ход с предложением
            resp = await client.post(f"/api/agents/{agent_id}/messages", json={"content": "hi"})
            events = []
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    payload = json.loads(line[len("data:") :].strip())
                    events.append(payload.get("event"))
            assert "memory_suggestion" in events
            dto = (await client.get("/api/agents")).json()[0]
            assert dto["memory_suggestion"] == "паттерн X"
            # принять предложение
            resp = await client.post(f"/api/agents/{agent_id}/memory-suggestion/accept")
            assert resp.status_code == 200
            assert resp.json()["entries"] == ["паттерн X"]
            dto = (await client.get("/api/agents")).json()[0]
            assert dto["memory_suggestion"] is None
            # fork от сообщения 0
            resp = await client.post(f"/api/agents/{agent_id}/fork", json={"message_index": 0})
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["messages"]) == 1
            # fork с неверным индексом
            resp = await client.post(f"/api/agents/{agent_id}/fork", json={"message_index": 99})
            assert resp.status_code == 400

    asyncio.run(run())


def test_web_stream_tool_events_and_scratchpad(tmp_path) -> None:
    """SSE-ход с tool-раундом: tool_message (assistant+tool), scratchpad, done по порядку."""
    llm = RoundLLM(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="c1", name="write_scratchpad", args='{"text": "шаги"}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="готово"), ChatChunk(finish_reason="stop")],
        ]
    )
    _state, app = build_state(tmp_path, llm=llm)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            agent = (await client.post("/api/agents", json={"name": "t"})).json()
            resp = await client.post(
                f"/api/agents/{agent['id']}/messages", json={"content": "делай"}
            )
            events = []
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[len("data:") :].strip()))
            names = [e["event"] for e in events]
            assert "tool_message" in names
            assert "scratchpad" in names
            assert names.index("tool_message") < names.index("done")
            tool_events = [e for e in events if e["event"] == "tool_message"]
            assert [e["message"]["role"] for e in tool_events] == ["assistant", "tool"]
            assert tool_events[0]["message"]["tool_calls"] == ["write_scratchpad"]
            assert tool_events[1]["message"]["tool_name"] == "write_scratchpad"
            assert tool_events[0]["message"]["id"] == "m1"
            assert tool_events[1]["message"]["id"] == "m2"
            scratch = [e for e in events if e["event"] == "scratchpad"]
            assert scratch and scratch[0]["content"] == "шаги"
            done_msg = next(e["message"] for e in events if e["event"] == "done")
            assert done_msg["content"] == "готово"
            assert done_msg["id"] == "m3"

    asyncio.run(run())


def test_web_stream_invariants_event(tmp_path) -> None:
    """SSE-ход с tool-раундом: появление инварианта шлёт событие 'invariants'."""
    llm = RoundLLM(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(
                            index=0,
                            id="i1",
                            name="invariant_add",
                            args='{"text": "не удалять лог"}',
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="готово"), ChatChunk(finish_reason="stop")],
        ]
    )
    _state, app = build_state(tmp_path, llm=llm)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            agent = (await client.post("/api/agents", json={"name": "t"})).json()
            resp = await client.post(
                f"/api/agents/{agent['id']}/messages", json={"content": "делай"}
            )
            events = []
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[len("data:") :].strip()))
            names = [e["event"] for e in events]
            assert "invariants" in names
            inv = next(e for e in events if e["event"] == "invariants")
            assert inv["invariants"] == ["не удалять лог"]
            dto = (await client.get("/api/agents")).json()[0]
            assert dto["invariants"] == ["не удалять лог"]

    asyncio.run(run())


def test_stream_deltas_resume_after_tool_round(tmp_path) -> None:
    """После tool-раунда дельты нового раунда стримятся с нуля, даже если текст короче."""
    llm = RoundLLM(
        [
            [
                ChatChunk(content="длинный текст первого раунда"),
                ChatChunk(
                    tool_call_deltas=[
                        _tcd(index=0, id="c1", name="write_scratchpad", args='{"text": "x"}')
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="ok"), ChatChunk(finish_reason="stop")],
        ]
    )
    _state, app = build_state(tmp_path, llm=llm)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            agent = (await client.post("/api/agents", json={"name": "t"})).json()
            resp = await client.post(
                f"/api/agents/{agent['id']}/messages", json={"content": "делай"}
            )
            events = []
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    events.append(json.loads(line[len("data:") :].strip()))
            # дельты после последнего tool_message покрывают короткий ответ целиком
            last_tool = max(i for i, e in enumerate(events) if e["event"] == "tool_message")
            tail = "".join(
                e["content"] for e in events[last_tool:] if e["event"] == "delta"
            )
            assert "ok" in tail
            done_msg = next(e["message"] for e in events if e["event"] == "done")
            assert done_msg["content"] == "ok"

    asyncio.run(run())


def test_web_scratchpad_endpoint(tmp_path) -> None:
    _state, app = build_state(tmp_path)

    async def run() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"  # type: ignore[arg-type]
        ) as client:
            agent = (await client.post("/api/agents", json={"name": "t"})).json()
            resp = await client.put(
                f"/api/agents/{agent['id']}/scratchpad", json={"content": "заметка"}
            )
            assert resp.status_code == 200
            dto = (await client.get("/api/agents")).json()[0]
            assert dto["scratchpad"] == "заметка"

    asyncio.run(run())
