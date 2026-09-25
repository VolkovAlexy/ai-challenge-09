"""McpManager — менеджер MCP-серверов процесса: коннект, статус, инструменты.

Управляет жизненным циклом всех `McpAdapter` (по одному на имя сервера из
`config.mcp_servers`) и поставляет агенту набор динамических `Tool` от
включённых и доступных серверов. Статус определяется в фоне на старте,
переключение вкл/выкл — глобальное и хранится только в оперативке.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

from agent.config.schema import McpServer
from agent.tools.context import ToolContext
from agent.tools.mcp import McpAdapter
from agent.tools.registry import Tool, ToolRegistry, ToolResult

if TYPE_CHECKING:
    from agent.web_server.dto import McpDTO

__all__ = ["McpManager", "McpStatusTool"]


class McpManager:
    """Реестр MCP-соединений процесса и источник динамических инструментов."""

    def __init__(self, servers: dict[str, McpServer]) -> None:
        self._specs = servers
        self._adapters: dict[str, McpAdapter] = {}
        self._server_registries: dict[str, ToolRegistry] = {}
        self._status: dict[str, Literal["connecting", "available", "unavailable"]] = {
            name: "connecting" if spec.enabled else "unavailable"
            for name, spec in servers.items()
        }
        self._enabled: dict[str, bool] = {name: spec.enabled for name, spec in servers.items()}
        self._lock = asyncio.Lock()

    async def _probe(self, name: str) -> None:
        """Пробинг одного сервера: коннект + перечисление инструментов."""
        spec = self._specs[name]
        adapter = McpAdapter(name)
        try:
            await adapter.connect(spec)
            registry = ToolRegistry()
            await adapter.sync_tools(registry)
        except BaseException:
            with suppress(Exception):
                await adapter.close()
            self._status[name] = "unavailable"
            self._adapters.pop(name, None)
            self._server_registries.pop(name, None)
            return
        self._adapters[name] = adapter
        self._server_registries[name] = registry
        self._status[name] = "available"

    async def connect_all(self) -> None:
        """Пробинг всех серверов параллельно (вызывается фоново при старте)."""
        for name in self._specs:
            self._status[name] = "connecting" if self._enabled[name] else "unavailable"
        await asyncio.gather(*(self._probe(name) for name in self._specs if self._enabled[name]))

    async def probe(self, name: str) -> None:
        """Повторный пробинг одного сервера (например, при включении)."""
        if self._status.get(name) == "available":
            return
        async with self._lock:
            self._status[name] = "connecting"
        await self._probe(name)

    async def set_enabled(self, name: str, enabled: bool) -> None:
        """Включить/выключить сервер (глобально на процесс)."""
        self._enabled[name] = enabled
        if enabled and self._status.get(name) != "available":
            await self.probe(name)

    def enabled_tools(self) -> list[Tool]:
        """Инструменты включённых и доступных серверов."""
        tools: list[Tool] = []
        for name in self._specs:
            if not self._enabled.get(name, False):
                continue
            if self._status.get(name) != "available":
                continue
            registry = self._server_registries.get(name)
            if registry is None:
                continue
            tools.extend(registry.all())
        return tools

    def tool_count(self, name: str) -> int:
        """Количество инструментов сервера (0 — сервер недоступен)."""
        registry = self._server_registries.get(name)
        return len(registry) if registry is not None else 0

    def server_tools(self, name: str) -> list[dict[str, str]]:
        """Имена и описания инструментов сервера ([] — сервер недоступен)."""
        registry = self._server_registries.get(name)
        if registry is None:
            return []
        return [{"name": tool.name, "description": tool.description} for tool in registry.all()]

    def dto_list(self) -> list[McpDTO]:
        """Состояние всех серверов для фронтенда."""
        from agent.web_server.dto import McpDTO, McpToolDTO

        return [
            McpDTO(
                name=name,
                transport=spec.transport,
                status=self._status.get(name, "connecting"),
                enabled=self._enabled.get(name, True),
                tool_count=self.tool_count(name),
                tools=[McpToolDTO(**tool) for tool in self.server_tools(name)],
            )
            for name, spec in self._specs.items()
        ]

    async def stop(self) -> None:
        """Закрыть все соединения."""
        async with self._lock:
            adapters = list(self._adapters.values())
            self._adapters.clear()
            self._server_registries.clear()
        await asyncio.gather(*(adapter.close() for adapter in adapters), return_exceptions=True)


class McpStatusTool:
    """Инструмент `mcp_status`: сообщает агенту о состоянии MCP-серверов.

    Динамически читает `McpManager` и возвращает по каждому серверу имя,
    транспорт, доступность и число инструментов — модель может ответить
    на «какие MCP-серверы доступны?» по факту, а не по памяти.
    """

    name = "mcp_status"
    description = (
        "Показывает список настроенных MCP-серверов и их состояние: имя, транспорт, "
        "доступность (доступен/недоступен/подключение), количество инструментов."
    )

    def __init__(self, manager: McpManager) -> None:
        self._manager = manager
        self.parameters: dict[str, Any] = {}

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        dtos = self._manager.dto_list()
        if not dtos:
            return ToolResult(output="MCP-серверы не настроены.")
        lines = []
        for dto in dtos:
            status = {
                "connecting": "подключение",
                "available": "доступен",
                "unavailable": "недоступен",
            }.get(dto.status, dto.status)
            mode = "вкл" if dto.enabled else "выкл"
            lines.append(
                f"- {dto.name}: {status}, транспорт {dto.transport}, "
                f"{dto.tool_count} инстр., {mode}"
            )
        return ToolResult(output="MCP-серверы:\n" + "\n".join(lines))
