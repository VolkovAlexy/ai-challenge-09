"""Mock API + MCP: агент вызывает MCP-инструмент, который идёт в mock REST API.

Полный круг: `running_mock_api()` поднимает свежий mock REST API (uvicorn);
`McpManager` подключает к нему MCP-сервер (`mcp_demo.mcp_mock`) по stdio —
тот спawn'ится из `sys.executable -m mcp_demo.mcp_mock --api-base <url>`.
Инструменты MCP-сервера попадают в агента динамически, модель решает вызвать
`add`, агент исполняет его через McpAdapter, MCP-сервер ходит по HTTP в mock API,
результат уходит в историю и используется в финальном ответе.
"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn

import mcp_demo.mock_api as mock_api
from agent.config.schema import McpServer, validate_config
from agent.core.message import ChatChunk, ToolCallDelta
from agent.memory.persistence import SessionStore
from agent.tools.context import ToolContext
from agent.tools.mcp_manager import McpManager
from agent.tools.registry import ToolRegistry
from agent.web_server.app import create_app
from agent.web_server.state import WebState
from tests.test_web_server import SeqMockLLM, collect_sse

_MOCK_URL_PREFIX = "http://127.0.0.1"
_MOCK_DESC = "[MCP:mock_math] Складывает два числа через mock API и возвращает сумму."


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": {"m1": 32768}},
            },
            "default_model": "p1:m1",
        }
    )


def free_port() -> int:
    """Свободный порт (привязка на 0; tiny race — приемлемо для теста)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def running_mock_api() -> AsyncIterator[str]:
    """Поднимает свежий mock REST API на свободном порту, отдаёт base URL."""
    port = free_port()
    cfg = uvicorn.Config(mock_api.make_app(), host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(cfg)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("mock api не поднялся")
        yield f"{_MOCK_URL_PREFIX}:{port}"
    finally:
        server.should_exit = True
        await task


def make_state(api_base: str, llm: SeqMockLLM) -> WebState:
    """WebState с MCP-сервером над mock API (stdio-транспорт к модулю mcp_mock)."""
    store = SessionStore(Path(":memory:"))
    manager = McpManager(
        {
            "mock_math": McpServer(
                transport="stdio",
                command=sys.executable,
                args=["-m", "mcp_demo.mcp_mock", "--api-base", api_base],
            )
        }
    )
    return WebState(
        config=make_config(),  # type: ignore[arg-type]
        llm=llm,  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=store,
        default_system_prompt="SP",
        mcp=manager,
    )


def tool_add_seq() -> list[ChatChunk]:
    """Последовательность: модель вызывает `add(5, 3)`, затем отвечает результатом."""
    return [
        ChatChunk(
            tool_call_deltas=[
                ToolCallDelta(
                    index=0,
                    id="call_add",
                    function_name="add",
                    function_arguments='{"a":5,"b":3}',
                )
            ]
        ),
        ChatChunk(finish_reason="tool_calls"),
    ]


async def test_mock_mcp_registers_tool_with_params() -> None:
    """Инструмент MCP-сервера зарегистрирован, входные параметры описаны."""
    async with running_mock_api() as api_base:
        llm = SeqMockLLM(
            [tool_add_seq(), [ChatChunk(content="8"), ChatChunk(finish_reason="stop")]]
        )
        state = make_state(api_base, llm)
        await state.start_mcp()
        try:
            record = state.create_agent()
            tool = record.agent._tools.get("add")
            assert tool is not None
            assert tool.description == _MOCK_DESC
            props = tool.parameters["properties"]
            assert props["a"]["description"] == "Первое слагаемое"
            assert props["b"]["description"] == "Второе слагаемое"
            assert set(tool.parameters["required"]) == {"a", "b"}
            # панель MCP видит сервер и инструмент
            dto = state.list_mcp()[0]
            assert dto.name == "mock_math"
            assert dto.status == "available"
            assert dto.tool_count == 2
            assert [t.name for t in dto.tools] == ["add", "hello"]
        finally:
            await state.mcp.stop()  # type: ignore[union-attr]
            await state.llm.close()


async def test_agent_calls_mock_mcp_tool_and_uses_result() -> None:
    """Агент вызывает MCP-инструмент `add`, MCP-сервер идёт в mock API, результат используется."""
    async with running_mock_api() as api_base:
        llm = SeqMockLLM(
            [tool_add_seq(), [ChatChunk(content="Сумма равна 8."), ChatChunk(finish_reason="stop")]]
        )
        state = make_state(api_base, llm)
        await state.start_mcp()
        try:
            app = create_app(state)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = (await client.post("/api/agents", json={"name": "test"})).json()
                agent_id = body["id"]
                async with client.stream(
                    "POST", f"/api/agents/{agent_id}/messages", json={"content": "5 + 3?"}
                ) as resp:
                    assert resp.status_code == 200
                    events = await collect_sse(resp)
                msgs = (await client.get(f"/api/agents/{agent_id}/messages")).json()
            # ход: user -> assistant(tool_call add) -> tool(8) -> assistant(использует результат)
            names = [m["role"] for m in msgs]
            assert names == ["user", "assistant", "tool", "assistant"]
            assert msgs[1]["tool_calls"] == ["add"]
            assert msgs[2]["tool_name"] == "add"
            assert msgs[2]["content"] == "8"
            assert msgs[3]["content"] == "Сумма равна 8."
            # результат инструмента доехал до SSE-стрима
            done = [payload for name, payload in events if name == "done"]
            assert done and done[0]["message"]["content"] == "Сумма равна 8."
        finally:
            await state.mcp.stop()  # type: ignore[union-attr]
            await state.llm.close()


async def test_mcp_status_reports_mock_server() -> None:
    """`mcp_status` сообщает агенту о доступном MCP-сервере над mock API."""
    async with running_mock_api() as api_base:
        state = make_state(api_base, SeqMockLLM([]))
        await state.start_mcp()
        try:
            record = state.create_agent()
            tool = record.agent._tools.get("mcp_status")
            assert tool is not None
            ctx = ToolContext(
                session=record.agent.memory,
                agent=record.agent,
                project_id=record.agent.project_id,
                run_id="r1",
            )
            result = await tool.execute({}, ctx)
            assert "mock_math" in result.output
            assert "доступен" in result.output
            assert "2 инстр." in result.output
        finally:
            await state.mcp.stop()  # type: ignore[union-attr]
            await state.llm.close()
