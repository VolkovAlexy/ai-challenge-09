"""Инструменты рабочей памяти (scratchpad) для function-calling.

Scratchpad — заметки агента по текущей задаче (уровень сессии). Инструменты
привязываются к конкретной `InMemorySession`, поэтому регистрируются в
per-agent реестре (общий ToolRegistry процесса они не получают).
"""

from __future__ import annotations

import json
from typing import Any

from agent.memory.session import InMemorySession
from agent.tools.context import ToolContext
from agent.tools.registry import Tool, ToolResult

SCRATCHPAD_LIMIT = 8000  # символов; защита от раздувания контекста

_SCRATCHPAD_NOTE = (
    "Рабочая память обновлена. Она добавляется в контекст при каждом запросе, "
    "поэтому пиши туда только важное для текущей задачи (план, промежуточные "
    "результаты, что сделать дальше)."
)

_WRITE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Новое содержимое рабочей памяти (заменяет прежнее)",
        }
    },
    "required": ["text"],
}

_APPEND_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "Заметка, добавляемая в конец рабочей памяти",
        }
    },
    "required": ["text"],
}

_READ_PARAMS: dict[str, Any] = {"type": "object", "properties": {}}


class WriteScratchpadTool:
    """write_scratchpad: заменяет содержимое scratchpad целиком."""

    name = "write_scratchpad"
    description = (
        "Заменить заметки рабочей памяти (scratchpad) целиком новым текстом. "
        "Используй для плана задачи, промежуточных результатов и следующих шагов."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _WRITE_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(arguments.get("text", ""))
        if not text.strip():
            return ToolResult(output="Ошибка: text не может быть пустым", is_error=True)
        self._memory.scratchpad = text.strip()[:SCRATCHPAD_LIMIT]
        return ToolResult(output=_SCRATCHPAD_NOTE)


class AppendScratchpadTool:
    """append_scratchpad: дописывает заметку в конец scratchpad."""

    name = "append_scratchpad"
    description = (
        "Дописать заметку в конец рабочей памяти (scratchpad). "
        "Для точечных добавлений: факты по задаче, результаты, шаги."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _APPEND_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(arguments.get("text", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: text не может быть пустым", is_error=True)
        current = self._memory.scratchpad
        combined = (current + "\n" + text).strip() if current else text
        self._memory.scratchpad = combined[:SCRATCHPAD_LIMIT]
        return ToolResult(output=_SCRATCHPAD_NOTE)


class ReadScratchpadTool:
    """read_scratchpad: возвращает текущее содержимое scratchpad."""

    name = "read_scratchpad"
    description = "Прочитать текущее содержимое рабочей памяти (scratchpad)."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _READ_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if not self._memory.scratchpad:
            return ToolResult(output="(рабочая память пуста)")
        return ToolResult(output=self._memory.scratchpad)


def scratchpad_tools(memory: InMemorySession) -> list[Tool]:
    """Создаёт инструменты scratchpad, привязанные к сессии агента."""
    tools: list[Tool] = [
        WriteScratchpadTool(memory),
        AppendScratchpadTool(memory),
        ReadScratchpadTool(memory),
    ]
    return tools


def parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Разбирает JSON-аргументы tool_call; ошибка → ValueError с понятным текстом."""
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"аргументы не являются корректным JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("аргументы tool_call должны быть JSON-объектом")
    return data
