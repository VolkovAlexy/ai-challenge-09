"""ToolRegistry — реестр инструментов агента.

Контракт зафиксирован: MCP-адаптер и другие источники инструментов
будут регистрировать свои `Tool` в общий реестр, который `Agent.ask()`
использует для исполнения tool_calls и формирования `tools` в запросе.

Реестр разделён на два пула: статические инструменты (встроенные,
привязанные к агенту/сессии) и динамические (внешние, например из MCP).
Статический инструмент затеняет динамический с тем же именем.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from agent.tools.context import ToolContext


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

    async def execute(
        self, arguments: dict[str, Any], ctx: ToolContext
    ) -> ToolResult: ...


class ToolRegistry:
    """Реестр инструментов: статические и динамические пулы.

    Статические инструменты регистрируются через `register`, динамические
    (MCP и прочие внешние источники) — через `register_dynamic`. Правило
    приоритета: статический инструмент затеняет динамический с тем же
    именем — динамическая регистрация игнорируется.
    """

    def __init__(self) -> None:
        self._static: dict[str, Tool] = {}
        self._dynamic: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Добавляет статический инструмент (повторное имя — перезапись).

        Статический инструмент вытесняет одноимённый динамический, то есть
        фактически затеняет его во всех операциях реестра.
        """
        self._static[tool.name] = tool
        self._dynamic.pop(tool.name, None)

    def register_dynamic(self, tool: Tool) -> None:
        """Добавляет динамический инструмент; статический с тем же именем затеняет его."""
        if tool.name in self._static:
            return
        self._dynamic[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._static.pop(name, None)
        self._dynamic.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Возвращает инструмент по имени (статический приоритетнее) или None."""
        return self._static.get(name) or self._dynamic.get(name)

    def all(self) -> Sequence[Tool]:
        """Все зарегистрированные инструменты (статические, затем динамические)."""
        return tuple(self._static.values()) + tuple(self._dynamic.values())

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
            for tool in self.all()
        ]

    def __len__(self) -> int:
        return len(self._static) + len(self._dynamic)
