"""ToolRegistry — задел под инструменты (в v1 пуст, реализация позже).

Контракт зафиксирован: MCP-адаптер и другие источники инструментов
будут регистрировать свои `Tool` в общий реестр, который `Agent.ask()`
использует для исполнения tool_calls и формирования `tools` в запросе.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Результат исполнения инструмента."""

    output: str
    is_error: bool = False


@runtime_checkable
class Tool(Protocol):
    """Инструмент, доступный агенту (контракт OpenAI function-calling)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema аргументов

    async def execute(self, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    """Реестр инструментов. Пустой в v1 — MCP-серверы зарегистрируют свои."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Добавляет инструмент (повторное имя — перезапись)."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Возвращает инструмент по имени или None."""
        return self._tools.get(name)

    def all(self) -> Sequence[Tool]:
        """Все зарегистрированные инструменты."""
        return tuple(self._tools.values())

    def to_api_tools(self) -> list[dict[str, Any]]:
        """Сериализация в формат OpenAI-параметра `tools` (для будущих запросов)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def __len__(self) -> int:
        return len(self._tools)
