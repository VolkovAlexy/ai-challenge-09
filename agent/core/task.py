"""Конечный автомат состояния задачи.

Формализует «эту задачу» как автомат с фазами IDLE → PLANNING → EXECUTION
→ VALIDATION → DONE и ортогональным флагом паузы (пауза возможна на любом
этапе). Состояние живёт в plain-Python классе (вне UI) и персистится вместе с
сессией, как scratchpad: фаза, текущий шаг и ожидаемое действие попадают в
LLM-контекст на каждом ходу — так модель «продолжает без повторных объяснений».
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PHASE_LABELS: dict[str, str] = {
    "idle": "без задачи",
    "planning": "планирование",
    "execution": "выполнение",
    "validation": "проверка",
    "done": "готово",
}


class TaskPhase(StrEnum):
    """Фазы конечного автомата задачи."""

    IDLE = "idle"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"


class InvalidTaskTransition(Exception):
    """Недопустимый переход между фазами."""

    def __init__(self, current: TaskPhase, target: TaskPhase) -> None:
        self.current = current
        self.target = target
        super().__init__(f"недопустимый переход {current} → {target}")


# Таблица допустимых переходов (единственный источник правды для валидации).
_ALLOWED: dict[TaskPhase, frozenset[TaskPhase]] = {
    TaskPhase.IDLE: frozenset({TaskPhase.PLANNING}),
    TaskPhase.PLANNING: frozenset({TaskPhase.EXECUTION, TaskPhase.IDLE}),
    TaskPhase.EXECUTION: frozenset(
        {TaskPhase.VALIDATION, TaskPhase.PLANNING, TaskPhase.IDLE}
    ),
    TaskPhase.VALIDATION: frozenset(
        {TaskPhase.EXECUTION, TaskPhase.DONE, TaskPhase.IDLE}
    ),
    TaskPhase.DONE: frozenset({TaskPhase.IDLE}),
}


@dataclass
class TaskTransition:
    """Журнал перехода: из какого этапа, в какой, с какой заметкой."""

    from_phase: TaskPhase
    to_phase: TaskPhase
    note: str = ""


@dataclass
class TaskState:
    """Состояние задачи (сериализуемое, сессионное).

    `step` — номер текущего шага (1-based); `0`, если шаги ещё не заданы.
    `steps` — план задачи; `expected_action` — что сделать дальше (на него
    модель опирается при продолжении после паузы).
    """

    phase: TaskPhase = TaskPhase.IDLE
    description: str = ""
    steps: list[str] = field(default_factory=list)
    step: int = 0
    expected_action: str = ""
    paused: bool = False
    history: list[TaskTransition] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """Активная ли задача (фаза отлична от IDLE)."""
        return self.phase != TaskPhase.IDLE

    def label(self) -> str:
        """Русская подпись текущей фазы (для UI/контекста)."""
        return PHASE_LABELS.get(self.phase.value, self.phase.value)

    @property
    def step_text(self) -> str:
        """Текст текущего шага ('' — шаги не заданы)."""
        if self.step <= 0 or not self.steps:
            return ""
        return self.steps[min(self.step - 1, len(self.steps) - 1)]

    def describe(self) -> str:
        """Текстовое представление для LLM-контекста; '' — задачи нет."""
        if not self.is_active:
            return ""
        lines = [f"Этап: {self.label()}"]
        if self.steps:
            lines.append(f"Шаг: {self.step} из {len(self.steps)}")
            if self.step_text:
                lines.append(f"Текущий шаг: {self.step_text}")
        if self.description:
            lines.append(f"Описание: {self.description}")
        if self.expected_action:
            lines.append(f"Ожидаемое действие: {self.expected_action}")
        lines.append(f"Пауза: {'да' if self.paused else 'нет'}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "description": self.description,
            "steps": list(self.steps),
            "step": self.step,
            "expected_action": self.expected_action,
            "paused": self.paused,
            "history": [
                {
                    "from_phase": item.from_phase.value,
                    "to_phase": item.to_phase.value,
                    "note": item.note,
                }
                for item in self.history
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TaskState:
        """Восстанавливает состояние из dict (при повреждённом — IDLE)."""
        if not data:
            return cls()
        try:
            phase = TaskPhase(str(data.get("phase", "idle")))
        except ValueError:
            phase = TaskPhase.IDLE
        steps = [str(s) for s in data.get("steps", [])]
        history: list[TaskTransition] = []
        for item in data.get("history", []):
            try:
                history.append(
                    TaskTransition(
                        from_phase=TaskPhase(str(item.get("from_phase", "idle"))),
                        to_phase=TaskPhase(str(item.get("to_phase", "idle"))),
                        note=str(item.get("note", "")),
                    )
                )
            except ValueError:
                continue
        try:
            step = int(data.get("step", 0))
        except (ValueError, TypeError):
            step = 0
        return cls(
            phase=phase,
            description=str(data.get("description", "")),
            steps=steps,
            step=step,
            expected_action=str(data.get("expected_action", "")),
            paused=bool(data.get("paused", False)),
            history=history,
        )


class TaskStateMachine:
    """Конечный автомат задачи: владеет `TaskState`, валидирует переходы."""

    def __init__(self, state: TaskState | None = None) -> None:
        self.state = state or TaskState()

    def transition(
        self,
        target: TaskPhase,
        *,
        expected_action: str | None = None,
        note: str = "",
    ) -> TaskState:
        """Переводит автомат в фазу `target`; ошибка — переход запрещён."""
        allowed = _ALLOWED[self.state.phase]
        if target not in allowed:
            raise InvalidTaskTransition(self.state.phase, target)
        self.state.history.append(TaskTransition(self.state.phase, target, note))
        self.state.phase = target
        if expected_action is not None:
            self.state.expected_action = expected_action
        return self.state

    def start(
        self,
        description: str,
        steps: list[str] | None = None,
        *,
        expected_action: str = "",
    ) -> TaskState:
        """Запускает новую задачу: сброс в IDLE → планирование."""
        self.reset()
        self.state.description = description
        self.state.steps = list(steps or [])
        self.state.step = 1 if self.state.steps else 0
        action = expected_action or "составить план"
        self.state.expected_action = action
        self.transition(TaskPhase.PLANNING, note="задача запущена")
        return self.state

    def to_planning(self, expected_action: str | None = None) -> TaskState:
        return self.transition(
            TaskPhase.PLANNING, expected_action=expected_action, note="план уточняется"
        )

    def to_execution(self, expected_action: str | None = None) -> TaskState:
        return self.transition(
            TaskPhase.EXECUTION, expected_action=expected_action, note="план готов"
        )

    def to_validation(self, expected_action: str | None = None) -> TaskState:
        return self.transition(
            TaskPhase.VALIDATION,
            expected_action=expected_action,
            note="выполнение завершено",
        )

    def to_done(self, expected_action: str | None = None) -> TaskState:
        return self.transition(
            TaskPhase.DONE, expected_action=expected_action, note="проверка пройдена"
        )

    def advance_step(self, expected_action: str = "") -> TaskState:
        """Переходит к следующему шагу плана (внутри этапа)."""
        self.state.step += 1
        if self.state.steps and self.state.step > len(self.state.steps):
            self.state.step = len(self.state.steps)
        if expected_action:
            self.state.expected_action = expected_action
        return self.state

    def set_expected_action(self, text: str) -> TaskState:
        self.state.expected_action = text
        return self.state

    def pause(self) -> TaskState:
        self.state.paused = True
        return self.state

    def resume(self) -> TaskState:
        self.state.paused = False
        return self.state

    def reset(self) -> TaskState:
        self.state = TaskState()
        return self.state

    def describe(self) -> str:
        """Текстовое представление для LLM-контекста; '' — задачи нет."""
        return self.state.describe()
