"""Инструменты ограничений (инвариантов) для function-calling.

Инварианты — обязательные требования к результату задачи: что должно быть
выполнено и что делать нельзя. Их список всегда добавляется в контекст и
является главным критерием качества на этапе ПРОВЕРКИ (см. PHASE_PROTOCOL).

Инструменты привязываются к конкретной `InMemorySession`, поэтому
регистрируются в per-agent реестре (как scratchpad и task).
"""

from __future__ import annotations

from typing import Any

from agent.memory.session import InMemorySession
from agent.tools.registry import Tool, ToolResult

INVARIANTS_LIMIT = 50  # защита от раздувания контекста

_INVARIANT_NOTE = (
    "Список ограничений обновлён. Он добавляется в контекст при каждом запросе "
    "и является главным критерием качества на этапе проверки результата: "
    "каждое ограничение должно быть выполнено или осознанно пересмотрено."
)

_ADD_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Текст ограничения: что обязательно или что нельзя делать",
        }
    },
    "required": ["text"],
}

_REMOVE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {
            "type": "integer",
            "description": "Номер ограничения в списке (1-based) для удаления",
        }
    },
    "required": ["index"],
}

_EMPTY_PARAMS: dict[str, Any] = {"type": "object", "properties": {}}


def _invariant_text(memory: InMemorySession) -> str:
    """Нумерованный список ограничений для контекста; '(ограничений нет)' — пусто."""
    if not memory.invariants:
        return "(ограничений нет)"
    return "\n".join(f"{i}. {text}" for i, text in enumerate(memory.invariants, start=1))


def _note(text: str) -> str:
    return f"{_INVARIANT_NOTE}\n\n{text}"


class InvariantAddTool:
    """invariant_add: добавляет ограничение в список инвариантов."""

    name = "invariant_add"
    description = (
        "Добавить ограничение (инвариант): обязательное требование к результату — "
        "что должно быть выполнено или что нельзя делать. Используй, когда "
        "пользователь озвучил или уточнил условие."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _ADD_PARAMS

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: text не может быть пустым", is_error=True)
        if len(self._memory.invariants) >= INVARIANTS_LIMIT:
            return ToolResult(
                output=f"Ошибка: достигнут лимит ограничений ({INVARIANTS_LIMIT})",
                is_error=True,
            )
        self._memory.invariants.append(text)
        return ToolResult(output=_note(_invariant_text(self._memory)))


class InvariantRemoveTool:
    """invariant_remove: удаляет ограничение по номеру (1-based)."""

    name = "invariant_remove"
    description = (
        "Удалить ограничение по номеру из списка. Используй, когда условие устарело "
        "или пользователь его снял."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _REMOVE_PARAMS

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            index = int(arguments.get("index", 0))
        except (ValueError, TypeError):
            return ToolResult(output="Ошибка: index должен быть числом", is_error=True)
        if index < 1 or index > len(self._memory.invariants):
            return ToolResult(
                output=f"Ошибка: ограничения с номером {index} нет", is_error=True
            )
        self._memory.invariants.pop(index - 1)
        return ToolResult(output=_note(_invariant_text(self._memory)))


class InvariantListTool:
    """invariant_list: возвращает текущий список ограничений."""

    name = "invariant_list"
    description = "Прочитать текущий список ограничений (инвариантов)."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(output=_invariant_text(self._memory))


class InvariantClearTool:
    """invariant_clear: очищает список ограничений целиком."""

    name = "invariant_clear"
    description = "Удалить все ограничения (инварианты). Используй при сбросе задачи."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self._memory.invariants = []
        return ToolResult(output="Список ограничений очищен.")


def invariant_tools(memory: InMemorySession) -> list[Tool]:
    """Создаёт инструменты ограничений, привязанные к сессии агента."""
    tools: list[Tool] = [
        InvariantAddTool(memory),
        InvariantRemoveTool(memory),
        InvariantListTool(memory),
        InvariantClearTool(memory),
    ]
    return tools
