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

# Канонический протокол фаз: что делать на каждом этапе и когда переходить.
# Вставляется в LLM-контекст при активной задаче (ContextBuilder), чтобы модель
# не «залипала» в одной фазе и не выполняла производство раньше времени.
PHASE_PROTOCOL = (
    "Протокол фаз задачи (фаза — верхний уровень; план — её шаги):\n"
    "- ПЛАНИРОВАНИЕ: цель — составить или скорректировать план работы, а НЕ "
    "выполнять её. Сначала извлеки из запроса и уточнений все явные требования и "
    "ограничения пользователя и занеси каждое в «Ограничения» через invariant_add — "
    "это обязательные инварианты, единый источник правды для проверки результата. "
    "План состоит из двух списков: ШАГИ ВЫПОЛНЕНИЯ (steps) и ШАГИ ПРОВЕРКИ "
    "(validation_steps); шаги проверки должны явно опираться на занесённые инварианты. "
    "Опрашивай пользователя, уточняй детали, покажи план и дождись подтверждения. "
    "Без подтверждения план не начинает выполняться. После подтверждения "
    "пользователем — переходи в выполнение: set_phase(execution).\n"
    "- ВЫПОЛНЕНИЕ: выполняй ТОЛЬКО ШАГИ ВЫПОЛНЕНИЯ по порядку (task_advance_step с "
    "done=true после завершения шага). Каждый шаг можно декомпозировать на подшаги — "
    "они не отражаются в общем плане. Производственную работу поручай субагентам "
    "профилей через delegate(role, task, context), результат интегрируй и продвигай "
    "шаги. Когда все ШАГИ ВЫПОЛНЕНИЯ выполнены — переходи в проверку: "
    "set_phase(validation).\n"
    "- ПРОВЕРКА: выполняй ТОЛЬКО ШАГИ ПРОВЕРКИ (рецензирование результата, "
    "делегируй редактору/критику). Проверь результат против «Ограничений» "
    "(инвариантов из контекста): каждое требование должно быть выполнено или "
    "осознанно пересмотрено/отмечено; нарушенное требование — это правка, "
    "вернись в выполнение: set_phase(execution) (шаги выполнения начнутся заново). "
    "Если есть правки — вернись в выполнение: "
    "set_phase(execution) (шаги выполнения начнутся заново). Если план нужно "
    "скорректировать — вернись в планирование: set_phase(planning) и обнови план "
    "через task_update_plan. Если всё в порядке — заверши: set_phase(done).\n"
    "- ГОТОВО: верни пользователю отчёт о проделанной работе списком.\n"
    "Не выходи из ПЛАНИРОВАНИЯ в ВЫПОЛНЕНИЕ, пока план не подтверждён пользователем."
)


class TaskPhase(StrEnum):
    """Фазы конечного автомата задачи."""

    IDLE = "idle"
    PLANNING = "planning"
    EXECUTION = "execution"
    VALIDATION = "validation"
    DONE = "done"


class InvalidTaskTransition(Exception):
    """Недопустимый переход между фазами."""

    def __init__(
        self, current: TaskPhase, target: TaskPhase, *, reason: str = ""
    ) -> None:
        self.current = current
        self.target = target
        self.reason = reason
        if reason:
            super().__init__(f"недопустимый переход {current} → {target}: {reason}")
        else:
            super().__init__(f"недопустимый переход {current} → {target}")


# Таблица допустимых переходов (единственный источник правды для валидации).
_ALLOWED: dict[TaskPhase, frozenset[TaskPhase]] = {
    TaskPhase.IDLE: frozenset({TaskPhase.PLANNING}),
    TaskPhase.PLANNING: frozenset({TaskPhase.EXECUTION, TaskPhase.IDLE}),
    TaskPhase.EXECUTION: frozenset(
        {TaskPhase.VALIDATION, TaskPhase.PLANNING, TaskPhase.IDLE}
    ),
    TaskPhase.VALIDATION: frozenset(
        {TaskPhase.EXECUTION, TaskPhase.PLANNING, TaskPhase.DONE, TaskPhase.IDLE}
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
    validation_steps: list[str] = field(default_factory=list)
    step: int = 0
    expected_action: str = ""
    paused: bool = False
    plan_confirmed: bool = False
    history: list[TaskTransition] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """Активная ли задача (фаза отлична от IDLE)."""
        return self.phase != TaskPhase.IDLE

    def label(self) -> str:
        """Русская подпись текущей фазы (для UI/контекста)."""
        return PHASE_LABELS.get(self.phase.value, self.phase.value)

    def active_steps(self) -> list[str]:
        """Активный список шагов: в выполнении — `steps`, в проверке — `validation_steps`."""
        if self.phase in (TaskPhase.VALIDATION, TaskPhase.DONE):
            return self.validation_steps
        return self.steps

    @property
    def step_text(self) -> str:
        """Текст текущего шага ('' — шаги не заданы)."""
        active = self.active_steps()
        if self.step <= 0 or not active:
            return ""
        return active[min(self.step - 1, len(active) - 1)]

    @staticmethod
    def _steps_block(title: str, steps: list[str], step: int) -> str:
        """Форматирует список шагов с отметками «выполнено/текущий»."""
        if not steps:
            return ""
        lines = [f"{title} ({len(steps)}):"]
        for i, text in enumerate(steps, start=1):
            marker = ""
            if step and i < step:
                marker = " (выполнено)"
            elif step and i == step:
                marker = " (текущий)"
            lines.append(f"- {i}. {text}{marker}")
        return "\n".join(lines)

    def describe(self) -> str:
        """Текстовое представление для LLM-контекста; '' — задачи нет."""
        if not self.is_active:
            return ""
        active = self.active_steps()
        lines = [f"Этап: {self.label()}"]
        if active:
            lines.append(f"Шаг: {self.step} из {len(active)}")
            if self.step_text:
                lines.append(f"Текущий шаг: {self.step_text}")
        if self.phase in (TaskPhase.VALIDATION, TaskPhase.DONE):
            exec_step = len(self.steps) + 1
            if self.phase is TaskPhase.VALIDATION:
                val_step = self.step
            else:
                val_step = len(self.validation_steps) + 1
        elif self.phase is TaskPhase.PLANNING:
            exec_step = self.step
            val_step = 0
        else:
            exec_step = self.step
            val_step = 0
        block = self._steps_block("Шаги выполнения", self.steps, exec_step)
        if block:
            lines.append(block)
        block = self._steps_block("Шаги проверки", self.validation_steps, val_step)
        if block:
            lines.append(block)
        if self.phase is TaskPhase.PLANNING:
            lines.append(f"План подтверждён: {'да' if self.plan_confirmed else 'нет'}")
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
            "validation_steps": list(self.validation_steps),
            "step": self.step,
            "expected_action": self.expected_action,
            "paused": self.paused,
            "plan_confirmed": self.plan_confirmed,
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
        validation_steps = [str(s) for s in data.get("validation_steps", [])]
        return cls(
            phase=phase,
            description=str(data.get("description", "")),
            steps=steps,
            validation_steps=validation_steps,
            step=step,
            expected_action=str(data.get("expected_action", "")),
            paused=bool(data.get("paused", False)),
            plan_confirmed=bool(data.get("plan_confirmed", False)),
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
        """Переводит автомат в фазу `target`; ошибка — переход запрещён.

        Переход в текущую фазу — no-op: модель может повторно вызвать
        set_phase(фаза) после того, как её уже перевёл обработчик UI
        (например, «Подтвердить план» уже перевёл в execution).
        """
        if target is self.state.phase:
            return self.state
        allowed = _ALLOWED[self.state.phase]
        if target not in allowed:
            raise InvalidTaskTransition(self.state.phase, target)
        if (
            self.state.phase is TaskPhase.PLANNING
            and target is TaskPhase.EXECUTION
            and not self.state.plan_confirmed
        ):
            raise InvalidTaskTransition(
                self.state.phase,
                target,
                reason="план не подтверждён пользователем",
            )
        old_active = self.state.active_steps()
        self.state.history.append(TaskTransition(self.state.phase, target, note))
        self.state.phase = target
        new_active = self.state.active_steps()
        if old_active is not new_active:
            self.state.step = 1 if new_active else 0
        if target is TaskPhase.PLANNING:
            self.state.plan_confirmed = False
        if expected_action is not None:
            self.state.expected_action = expected_action
        return self.state

    def start(
        self,
        description: str,
        steps: list[str] | None = None,
        *,
        validation_steps: list[str] | None = None,
        expected_action: str = "",
    ) -> TaskState:
        """Запускает новую задачу: сброс в IDLE → планирование."""
        self.reset()
        self.state.description = description
        self.state.steps = list(steps or [])
        self.state.validation_steps = list(validation_steps or [])
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

    def advance_step(self, expected_action: str = "", *, done: bool = True) -> TaskState:
        """Переходит к следующему шагу активного плана (внутри этапа).

        `done=True` — текущий шаг считается выполненным и осуществляется переход
        дальше (счётчик шага увеличивается). `done=False` — только обновляет
        `expected_action`, не продвигая счётчик. Продвижение допустимо лишь в фазах
        выполнения и проверки. Когда достигнут последний шаг этапа, автомат
        переходит сам: выполнение → проверка, проверка → готово (шаги полного
        авто-прогона после подтверждения плана).
        """
        if self.state.phase not in (
            TaskPhase.EXECUTION,
            TaskPhase.VALIDATION,
        ):
            raise InvalidTaskTransition(
                self.state.phase,
                self.state.phase,
                reason="шаг можно продвигать только в фазах выполнения или проверки",
            )
        if done:
            self.state.step += 1
            active = self.state.active_steps()
            if active and self.state.step > len(active):
                self.state.step = len(active)
            if active and self.state.step >= len(active):
                if self.state.phase is TaskPhase.EXECUTION:
                    phase_note = (
                        expected_action
                        or "все шаги выполнения выполнены — проверь результат по ограничениям"
                    )
                    return self.transition(
                        TaskPhase.VALIDATION,
                        expected_action=phase_note,
                        note="выполнение завершено",
                    )
                phase_note = (
                    expected_action or "проверка пройдена — верни итоговый отчёт"
                )
                return self.transition(
                    TaskPhase.DONE,
                    expected_action=phase_note,
                    note="проверка пройдена",
                )
        if expected_action:
            self.state.expected_action = expected_action
        return self.state

    def set_plan(
        self,
        steps: list[str],
        *,
        description: str | None = None,
        validation_steps: list[str] | None = None,
    ) -> TaskState:
        """Заменяет план задачи (шаги выполнения и проверки), сбрасывает подтверждение.

        Используется для составления и корректировки плана (обычно на фазе
        планирования): новые шаги, возможно обновлённое описание. Шаг
        сбрасывается на первый (план пересобирается с нуля), подтверждение
        плана снимается — после правки нужно подтвердить план заново.
        """
        self.state.steps = [str(s) for s in steps if str(s).strip()]
        if validation_steps is not None:
            self.state.validation_steps = [
                str(s) for s in validation_steps if str(s).strip()
            ]
        self.state.step = 1 if self.state.steps else 0
        if description is not None:
            self.state.description = description
        self.state.plan_confirmed = False
        return self.state

    def set_plan_confirmed(self, confirmed: bool = True) -> TaskState:
        """Отмечает план как подтверждённый/неподтверждённый пользователем."""
        self.state.plan_confirmed = confirmed
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
