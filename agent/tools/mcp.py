"""McpAdapter — подключение MCP-сервера через официальный `mcp` Python SDK.

Поддерживаемые транспорты: `stdio` (локальный процесс по команде) и `http`
(streamable-http по URL). Адаптер перечисляет инструменты сервера и регистрирует
каждый как динамический `Tool` в общем `ToolRegistry`, которыми владеет Agent.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from agent.config.schema import McpServer
from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry, ToolResult

__all__ = ["McpAdapter", "McpConnection"]


class McpConnection(Protocol):
    """Соединение с MCP-сервером (контракт для зависимостей адаптера)."""

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...


class _McpTool:
    """Обёртка MCP-инструмента как Tool: `execute` делегирует в `call_tool` адаптера."""

    def __init__(
        self,
        adapter: McpAdapter,
        name: str,
        description: str,
        parameters: dict[str, Any],
    ) -> None:
        self._adapter = adapter
        self.name = name
        self.description = description
        self.parameters = parameters

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return await self._adapter.call_tool(self.name, self._inject_owner(arguments, ctx))

    def _inject_owner(
        self, arguments: dict[str, Any], ctx: ToolContext
    ) -> dict[str, Any]:
        """Дополняет аргументы идентификаторами владельца, если инструмент их объявляет.

        Планировщик (mcp_scheduler) декларирует `owner_agent_id`/`owner_session_id`/
        `owner_project_id` в схеме аргументов — тогда результат задания маршрутизируется
        в сессию создателя. Для остальных MCP-инструментов ничего не добавляется.
        """
        result = dict(arguments)
        properties = (self.parameters or {}).get("properties", {})
        for key, value in (
            ("owner_agent_id", ctx.agent_id),
            ("owner_session_id", ctx.session_id),
            ("owner_project_id", ctx.project_id),
        ):
            if key in properties:
                result[key] = value
        return result


class McpAdapter:
    """Адаптер MCP-сервера в ToolRegistry через `mcp` Python SDK."""

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._spec: McpServer | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    async def connect(self, spec: McpServer) -> None:
        """Подключиться по спецификации: `http` — streamable-http, иначе `stdio`."""
        if self._session is not None:
            raise RuntimeError("соединение уже установлено")
        stack = AsyncExitStack()
        try:
            if spec.transport == "http":
                streams = await stack.enter_async_context(
                    streamable_http_client(spec.url or "")
                )
            else:
                params = StdioServerParameters(command=spec.command or "", args=spec.args)
                streams = await stack.enter_async_context(stdio_client(params))
            read, write = streams
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except BaseException:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._spec = spec

    async def list_tools(self) -> list[dict[str, Any]]:
        """Список инструментов сервера: имя, описание, JSON-схема аргументов."""
        session = self._require_session()
        try:
            result = await session.list_tools()
        except Exception as exc:
            if not self._session_dead(exc):
                raise
            await self._reconnect()
            result = await self._require_session().list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.input_schema,
            }
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Вызвать инструмент сервера и смаппить результат в `ToolResult`."""
        session = self._require_session()
        try:
            result = await session.call_tool(name, arguments=arguments)
        except Exception as exc:
            if not self._session_dead(exc):
                raise
            await self._reconnect()
            result = await self._require_session().call_tool(name, arguments=arguments)
        is_error = bool(getattr(result, "is_error", False))
        content = getattr(result, "content", None)
        if isinstance(content, list):
            parts = [c.text for c in content if getattr(c, "type", "") == "text"]
            output = "\n".join(parts)
        else:
            output = str(content)
        return ToolResult(output=output, is_error=is_error)

    @staticmethod
    def _session_dead(exc: Exception) -> bool:
        """Является ли ошибка следствием мёртвой сессии (например, перезапуск сервера).

        Scheduler перезапускается в фоне, и тогда streamable-http сессия, которую
        держит адаптер, становится недействительной: клиент получает
        `MCPError(code=CONNECTION_CLOSED, message="Connection closed")`. Такие
        транспортные ошибки (обрыв/сброс соединения) неотличимы от «мёртвой»
        сессии по сути, поэтому распознаём их как повод переподключиться.
        """
        text = str(exc).lower()
        return any(
            phrase in text
            for phrase in (
                "session not found",
                "session terminated",
                "invalid session id",
                "session is no longer",
                "connection closed",
                "connection reset",
                "connection lost",
                "connection error",
                "stream closed",
                "socket closed",
                "read timeout",
                "broken pipe",
            )
        )

    async def _reconnect(self) -> None:
        """Переподключиться после перезапуска сервера: закрыть и создать сессию заново."""
        await self.close()
        if self._spec is not None:
            await self.connect(self._spec)

    async def sync_tools(self, registry: ToolRegistry) -> int:
        """Зарегистрировать инструменты сервера в `registry` как динамические."""
        count = 0
        for item in await self.list_tools():
            registry.register_dynamic(
                _McpTool(
                    self,
                    item["name"],
                    f"[MCP:{self.server_name}] {item['description']}".strip(),
                    item["parameters"],
                )
            )
            count += 1
        return count

    async def close(self) -> None:
        """Закрыть соединение (сессию и транспорт)."""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP-соединение не установлено")
        return self._session
