"""Инструменты состояния задачи (конечного автомата) для function-calling.

Автомат `InMemorySession.task` (TaskStateMachine) — фаза, текущий шаг и
ожидаемое действие. Инструменты привязываются к конкретной сессии, поэтому
регистрируются в per-agent реестре (общий ToolRegistry процесса они не
получают), как и scratchpad.
"""

from __future__ import annotations

from typing import Any

from agent.core.task import InvalidTaskTransition, TaskPhase
from agent.memory.session import InMemorySession
from agent.tools.context import ToolContext
from agent.tools.registry import Tool, ToolResult

# подписи фаз, которые модель может использовать в аргументах
_PHASE_ALIASES: dict[str, TaskPhase] = {
    "planning": TaskPhase.PLANNING,
    "execution": TaskPhase.EXECUTION,
    "validation": TaskPhase.VALIDATION,
    "done": TaskPhase.DONE,
    "планирование": TaskPhase.PLANNING,
    "выполнение": TaskPhase.EXECUTION,
    "проверка": TaskPhase.VALIDATION,
    "готово": TaskPhase.DONE,
}

_EMPTY_PARAMS: dict[str, Any] = {"type": "object", "properties": {}}

_START_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "Что нужно сделать (описание задачи)",
        },
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "План задачи: упорядоченные шаги ВЫПОЛНЕНИЯ (не пустой)",
        },
        "validation_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "План проверки: упорядоченные шаги ПРОВЕРКИ результата (необязательно)",
        },
        "expected_action": {
            "type": "string",
            "description": "Что сделать дальше (по умолчанию — составить план)",
        },
    },
    "required": ["description"],
}

_SET_PHASE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "phase": {
            "type": "string",
            "description": "Целевая фаза: planning | execution | validation | done",
        },
        "expected_action": {
            "type": "string",
            "description": "Что сделать дальше после перехода",
        },
    },
    "required": ["phase"],
}

_STEP_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_action": {
            "type": "string",
            "description": "Что сделать на следующем шаге",
        },
        "done": {
            "type": "boolean",
            "description": (
                "true — текущий шаг выполнен и можно перейти дальше; "
                "false — только указать ожидаемое действие"
            ),
        },
    },
}

_ACTION_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "expected_action": {
            "type": "string",
            "description": "Ожидаемое действие (что сделать дальше)",
        }
    },
    "required": ["expected_action"],
}

_UPDATE_PLAN_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Обновлённый план задачи: упорядоченные шаги ВЫПОЛНЕНИЯ (не пустой)",
        },
        "validation_steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Обновлённый план проверки: упорядоченные шаги ПРОВЕРКИ (необязательно)",
        },
        "description": {
            "type": "string",
            "description": "Обновлённое описание задачи (необязательно)",
        },
    },
    "required": ["steps"],
}


def _parse_phase(raw: str) -> TaskPhase:
    """Разбирает подпись фазы из аргумента; ValueError — неопознанная."""
    key = str(raw).strip().lower()
    phase = _PHASE_ALIASES.get(key)
    if phase is None:
        allowed = ", ".join(sorted(_PHASE_ALIASES))
        raise ValueError(f"неизвестная фаза '{raw}' (ожидается: {allowed})")
    return phase


def _state_text(memory: InMemorySession) -> str:
    text = memory.task.describe()
    return text if text else "Задача не задана. Начни с task_start."


def _note(text: str) -> str:
    return (
        f"Состояние задачи обновлено.\n{text}\n\n"
        "Оно добавляется в контекст при каждом запросе, поэтому следующее "
        "действие (ожидаемое) выбирай из актуального состояния."
    )


class TaskReadTool:
    """task_read: возвращает текущее состояние задачи (фаза, шаг, ожидаемое действие)."""

    name = "task_read"
    description = "Прочитать состояние текущей задачи: этап, текущий шаг, ожидаемое действие."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output=_state_text(self._memory))


class TaskStartTool:
    """task_start: запускает новую задачу (сброс → планирование)."""

    name = "task_start"
    description = (
        "Начать новую задачу: перевести автомат в планирование. "
        "Аргументы — описание задачи и упорядоченный план шагов."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _START_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        description = str(arguments.get("description", "")).strip()
        if not description:
            return ToolResult(output="Ошибка: description не может быть пустым", is_error=True)
        steps = [str(s) for s in arguments.get("steps", []) if str(s).strip()]
        validation_steps = [
            str(s) for s in arguments.get("validation_steps", []) if str(s).strip()
        ]
        expected_action = str(arguments.get("expected_action", "")).strip()
        self._memory.task.start(
            description,
            steps,
            validation_steps=validation_steps,
            expected_action=expected_action,
        )
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskSetPhaseTool:
    """task_set_phase: переводит автомат в фазу (валидирует переход)."""

    name = "task_set_phase"
    description = (
        "Перевести задачу на другой этап (planning/execution/validation/done). "
        "Переход проверяется конечным автоматом; при недопустимом — ошибка."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _SET_PHASE_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        try:
            phase = _parse_phase(arguments.get("phase", ""))
        except ValueError as exc:
            return ToolResult(output=f"Ошибка: {exc}", is_error=True)
        expected_action = str(arguments.get("expected_action", "")).strip()
        try:
            self._memory.task.transition(
                phase,
                expected_action=expected_action or None,
                note="переход по инструменту",
            )
        except InvalidTaskTransition as exc:
            return ToolResult(output=f"Ошибка: {exc}", is_error=True)
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskAdvanceStepTool:
    """task_advance_step: переход к следующему шагу плана."""

    name = "task_advance_step"
    description = (
        "Перейти к следующему шагу плана задачи и указать, что делать дальше."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _STEP_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        expected_action = str(arguments.get("expected_action", "")).strip()
        done = bool(arguments.get("done", True))
        try:
            self._memory.task.advance_step(expected_action, done=done)
        except InvalidTaskTransition as exc:
            return ToolResult(output=f"Ошибка: {exc}", is_error=True)
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskSetExpectedActionTool:
    """task_set_expected_action: задаёт ожидаемое действие (что сделать дальше)."""

    name = "task_set_expected_action"
    description = (
        "Указать ожидаемое действие — что агент/пользователь сделает дальше. "
        "Удобно для паузы и продолжения без повторных объяснений."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _ACTION_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(arguments.get("expected_action", "")).strip()
        if not text:
            return ToolResult(output="Ошибка: expected_action не может быть пустым", is_error=True)
        self._memory.task.set_expected_action(text)
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskPauseTool:
    """task_pause: приостанавливает задачу на текущем этапе."""

    name = "task_pause"
    description = "Приостановить задачу на текущем этапе (пауза возможна на любом этапе)."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self._memory.task.pause()
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskResumeTool:
    """task_resume: возобновляет задачу после паузы."""

    name = "task_resume"
    description = "Возобновить приостановленную задачу (продолжить с ожидаемого действия)."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self._memory.task.resume()
        return ToolResult(output=_note(self._memory.task.describe()))


class TaskResetTool:
    """task_reset: сбрасывает задачу в исходное состояние (без задачи)."""

    name = "task_reset"
    description = "Сбросить задачу: перевести автомат в исходное состояние (без задачи)."

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _EMPTY_PARAMS.copy()

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self._memory.task.reset()
        return ToolResult(output="Задача сброшена.")


class TaskUpdatePlanTool:
    """task_update_plan: заменяет план задачи (шаги) и описание, снимает подтверждение."""

    name = "task_update_plan"
    description = (
        "Заменить план задачи (список шагов) и, при необходимости, описание. "
        "Используется в фазе планирования, чтобы составить или скорректировать план "
        "после возврата из проверки. Подтверждение плана снимается — его нужно дать заново."
    )

    def __init__(self, memory: InMemorySession) -> None:
        self._memory = memory
        self.parameters = _UPDATE_PLAN_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        steps = [str(s) for s in arguments.get("steps", []) if str(s).strip()]
        if not steps:
            return ToolResult(output="Ошибка: steps не может быть пустым", is_error=True)
        validation_steps = (
            [str(s) for s in arguments.get("validation_steps", []) if str(s).strip()]
            if "validation_steps" in arguments
            else None
        )
        description = arguments.get("description")
        self._memory.task.set_plan(
            steps, description=description, validation_steps=validation_steps
        )
        return ToolResult(output=_note(self._memory.task.describe()))


def task_tools(memory: InMemorySession) -> list[Tool]:
    """Создаёт инструменты состояния задачи, привязанные к сессии агента."""
    tools: list[Tool] = [
        TaskReadTool(memory),
        TaskStartTool(memory),
        TaskSetPhaseTool(memory),
        TaskAdvanceStepTool(memory),
        TaskSetExpectedActionTool(memory),
        TaskPauseTool(memory),
        TaskResumeTool(memory),
        TaskResetTool(memory),
        TaskUpdatePlanTool(memory),
    ]
    return tools
