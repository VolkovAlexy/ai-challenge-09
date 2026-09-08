"""McpAdapter — задел под MCP (в v1 stub, реализация позже).

План: подключение MCP-сервера (транспорты stdio / streamable-http) через
официальный `mcp` Python SDK, перечисление его инструментов и маппинг на
`Tool` с ре-экспортом в общий `ToolRegistry`, которым уже владеет Agent.
"""

from __future__ import annotations

from typing import Any, Protocol

from my_agent.tools.registry import ToolRegistry, ToolResult


class McpConnection(Protocol):
    """Соединение с MCP-сервером (контракт, реализация — при подключении SDK)."""

    async def list_tools(self) -> list[Any]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


class McpAdapter:
    """Адаптер MCP-сервера в ToolRegistry (stub в v1).

    Будущий контракт:
    - `connect(spec)` — подключение по спецификации (stdio-команда или http-url);
    - `sync_tools(registry)` — перечислить инструменты сервера и зарегистрировать
      каждый как `Tool`, чей `execute` вызывает `call_tool`;
    - `close()` — закрыть соединение.
    """

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self._connection: McpConnection | None = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    def connect(self, spec: str) -> None:
        """Подключиться к MCP-серверу (stub: реализация при добавлении SDK)."""
        raise NotImplementedError("McpAdapter.connect — задел, реализация после v1")

    def sync_tools(self, registry: ToolRegistry) -> int:
        """Зарегистрировать инструменты сервера в registry (stub)."""
        raise NotImplementedError("McpAdapter.sync_tools — задел, реализация после v1")

    def close(self) -> None:
        """Закрыть соединение (stub: в v1 нечего закрывать)."""
        self._connection = None
