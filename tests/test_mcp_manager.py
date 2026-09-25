"""McpManager: пробинг серверов, статусы, вкл/выкл, проброс инструментов в агента."""

import sys
from pathlib import Path

import httpx
import pytest

from agent.config.schema import McpServer, validate_config
from agent.core.message import ChatChunk
from agent.memory.persistence import SessionStore
from agent.tools.context import ToolContext
from agent.tools.mcp_manager import McpManager
from agent.tools.registry import ToolRegistry
from agent.web_server.app import create_app
from agent.web_server.state import WebState
from tests.test_mcp import _SERVER_SRC
from tests.test_web_server import MockLLM

_ADD_DESC = "[MCP:demo] Складывает два числа"


@pytest.fixture
def spec() -> McpServer:
    return McpServer(
        transport="stdio",
        command=sys.executable,
        args=["-c", _SERVER_SRC],
    )


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": {"m1": 32768}},
            },
            "default_model": "p1:m1",
        }
    )


def make_manager(spec: McpServer) -> McpManager:
    return McpManager({"demo": spec})


def make_state(spec: McpServer) -> WebState:
    store = SessionStore(Path(":memory:"))
    return WebState(
        config=make_config(),
        llm=MockLLM([ChatChunk(content="ok")]),  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=store,
        default_system_prompt="SP",
        mcp=make_manager(spec),
    )


async def test_connect_all_marks_available_and_lists_tools(spec: McpServer) -> None:
    mgr = make_manager(spec)
    await mgr.connect_all()
    assert mgr._status["demo"] == "available"
    dto = mgr.dto_list()[0]
    assert dto.name == "demo"
    assert dto.status == "available"
    assert dto.enabled is True
    assert dto.tool_count == 1
    assert [t.name for t in mgr.enabled_tools()] == ["add"]
    assert [(t.name, t.description) for t in dto.tools] == [("add", _ADD_DESC)]
    assert mgr.server_tools("demo") == [{"name": "add", "description": _ADD_DESC}]
    assert mgr.server_tools("nope") == []
    await mgr.stop()


async def test_unreachable_server_is_unavailable() -> None:
    un = McpServer(
        transport="stdio", command=sys.executable, args=["-c", "import sys; sys.exit(1)"]
    )
    mgr = make_manager(un)
    await mgr.connect_all()
    assert mgr._status["demo"] == "unavailable"
    assert mgr.tool_count("demo") == 0
    assert mgr.enabled_tools() == []
    await mgr.stop()


async def test_set_enabled_toggles_tool_provider(spec: McpServer) -> None:
    mgr = make_manager(spec)
    await mgr.connect_all()
    await mgr.set_enabled("demo", False)
    assert mgr.dto_list()[0].enabled is False
    assert mgr.enabled_tools() == []
    await mgr.set_enabled("demo", True)
    assert [t.name for t in mgr.enabled_tools()] == ["add"]
    await mgr.stop()


async def test_disabled_server_is_togglable_and_not_stuck_connecting(spec: McpServer) -> None:
    """Выключенный в конфиге сервер не должен зависнуть в 'connecting'.

    Раньше connect_all() пропускал такой сервер, но статус оставался 'connecting',
    а фронтенд блокировал тумблер при 'connecting' — включить было невозможно.
    """
    off = spec.model_copy(update={"enabled": False})
    mgr = make_manager(off)
    await mgr.connect_all()
    dto = mgr.dto_list()[0]
    assert dto.enabled is False
    assert dto.status == "unavailable"  # не 'connecting': тумблер остаётся кликабельным
    await mgr.set_enabled("demo", True)
    assert mgr._status["demo"] == "available"
    assert [t.name for t in mgr.enabled_tools()] == ["add"]
    await mgr.stop()


async def test_web_state_pushes_mcp_tools_into_agent(spec: McpServer) -> None:
    state = make_state(spec)
    await state.start_mcp()
    record = state.create_agent()
    assert record.agent._tools.get("add") is not None
    record.agent.sync_dynamic_tools([])
    assert record.agent._tools.get("add") is None
    await state.mcp.stop()  # type: ignore[union-attr]
    await state.llm.close()


async def test_mcp_status_tool_reports_servers(spec: McpServer) -> None:
    state = make_state(spec)
    await state.start_mcp()
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
    assert "demo" in result.output
    assert "доступен" in result.output
    assert "1 инстр." in result.output
    await state.mcp.stop()  # type: ignore[union-attr]
    await state.llm.close()


async def test_mcp_routes_via_http(spec: McpServer) -> None:
    state = make_state(spec)
    app = create_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await state.start_mcp()
        resp = await client.get("/api/mcp")
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["name"] == "demo"
        assert body[0]["status"] == "available"
        assert body[0]["tools"] == [{"name": "add", "description": _ADD_DESC}]
        resp = await client.patch("/api/mcp/demo", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()[0]["enabled"] is False
        resp = await client.patch("/api/mcp/nope", json={"enabled": True})
        assert resp.status_code == 404
    await state.mcp.stop()  # type: ignore[union-attr]
    await state.llm.close()
