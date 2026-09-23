"""McpAdapter: подключение к локальному stdio MCP-серверу + list/call round-trip."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from agent.config.schema import McpServer
from agent.tools.context import ToolContext
from agent.tools.mcp import McpAdapter
from agent.tools.registry import ToolRegistry, ToolResult

_SERVER_SRC = """
import asyncio, sys
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server import stdio

def make_server():
    async def on_list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="add",
                    description="Складывает два числа",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "a": {"type": "number", "description": "первое слагаемое"},
                            "b": {"type": "number", "description": "второе слагаемое"},
                        },
                        "required": ["a", "b"],
                    },
                )
            ]
        )

    async def on_call_tool(ctx, params):
        args = params.arguments or {}
        value = args.get("a", 0) + args.get("b", 0)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(value))]
        )

    return Server("demo", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

async def main():
    server = make_server()
    async with stdio.stdio_server() as (read, write):
        await server.run(
            read, write, server.create_initialization_options(), raise_exceptions=True
        )

asyncio.run(main())
"""


class FakeTool:
    """Минимальная реализация Tool с фиксированным именем."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


@pytest.fixture
def spec() -> McpServer:
    return McpServer(
        transport="stdio",
        command=sys.executable,
        args=["-c", _SERVER_SRC],
    )


@asynccontextmanager
async def connected(spec: McpServer) -> AsyncIterator[McpAdapter]:
    a = McpAdapter("demo")
    await a.connect(spec)
    try:
        yield a
    finally:
        await a.close()


async def test_connect_and_list_tools(spec: McpServer) -> None:
    async with connected(spec) as adapter:
        assert adapter.connected is True
        tools = await adapter.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "add"
        assert "Складывает" in tools[0]["description"]
        assert tools[0]["parameters"]["type"] == "object"


async def test_call_tool_round_trip(spec: McpServer) -> None:
    async with connected(spec) as adapter:
        result = await adapter.call_tool("add", {"a": 5, "b": 3})
        assert result.is_error is False
        assert result.output == "8"


async def test_sync_tools_registers_dynamic_and_shadowed_by_static(
    spec: McpServer,
) -> None:
    async with connected(spec) as adapter:
        reg = ToolRegistry()
        count = await adapter.sync_tools(reg)
        assert count == 1
        assert [t.name for t in reg.all()] == ["add"]
        tool = reg.get("add")
        assert tool is not None
        assert tool.parameters["type"] == "object"
        # статический инструмент с тем же именем затеняет динамический
        static = FakeTool("add")
        reg.register(static)
        await adapter.sync_tools(reg)
        assert reg.get("add") is static
        assert len(reg) == 1


async def test_operations_before_connect_raise() -> None:
    a = McpAdapter("demo")
    with pytest.raises(RuntimeError):
        await a.list_tools()
    with pytest.raises(RuntimeError):
        await a.call_tool("add", {"a": 1, "b": 2})
    await a.close()


async def test_double_connect_raises(spec: McpServer) -> None:
    a = McpAdapter("demo")
    await a.connect(spec)
    with pytest.raises(RuntimeError):
        await a.connect(spec)
    await a.close()


def test_mcp_server_schema_validation() -> None:
    # stdio без command — ошибка
    with pytest.raises(ValueError):
        McpServer(transport="stdio")
    # http без url — ошибка
    with pytest.raises(ValueError):
        McpServer(transport="http")
    # корректный stdio
    assert McpServer(command="uv").command == "uv"
    # корректный http
    assert McpServer(transport="http", url="http://x/mcp").url == "http://x/mcp"
