"""Конечный автомат состояния задачи: переходы, пауза, сериализация."""

import pytest

from agent.core.task import (
    PHASE_LABELS,
    InvalidTaskTransition,
    TaskPhase,
    TaskState,
    TaskStateMachine,
)


def test_default_state_is_idle() -> None:
    machine = TaskStateMachine()
    assert machine.state.phase == TaskPhase.IDLE
    assert not machine.state.is_active
    assert machine.describe() == ""


def test_start_sets_planning_and_steps() -> None:
    machine = TaskStateMachine()
    state = machine.start("написать модуль", ["а", "б", "в"])
    assert state.phase == TaskPhase.PLANNING
    assert state.is_active
    assert state.step == 1
    assert state.steps == ["а", "б", "в"]
    assert state.expected_action == "составить план"
    assert machine.describe() != ""


def test_happy_path_transitions() -> None:
    machine = TaskStateMachine()
    machine.start("задача")
    machine.set_plan_confirmed(True)
    machine.to_execution("реализовать")
    assert machine.state.phase == TaskPhase.EXECUTION
    machine.to_validation("проверить")
    assert machine.state.phase == TaskPhase.VALIDATION
    machine.to_done()
    assert machine.state.phase == TaskPhase.DONE


def test_invalid_transition_raises() -> None:
    machine = TaskStateMachine()
    machine.start("задача")
    # planning → done напрямую запрещён
    with pytest.raises(InvalidTaskTransition):
        machine.to_done()
    # idle → execution запрещён
    machine.reset()
    with pytest.raises(InvalidTaskTransition):
        machine.to_execution()


def test_can_regress_from_validation_to_execution() -> None:
    machine = TaskStateMachine()
    machine.start("задача")
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.to_validation()
    # нашли баг — возврат на выполнение
    machine.to_execution("исправить")
    assert machine.state.phase == TaskPhase.EXECUTION


def test_advance_step_clamps_to_plan_length() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["шаг1", "шаг2", "шаг3"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.advance_step()
    assert machine.state.step == 2
    assert machine.state.phase == TaskPhase.EXECUTION
    machine.advance_step()
    # последний шаг выполнения — авто-переход в проверку
    assert machine.state.phase == TaskPhase.VALIDATION
    assert machine.state.step == 0


def test_pause_any_phase_and_resume() -> None:
    for phase in (TaskPhase.PLANNING, TaskPhase.EXECUTION, TaskPhase.VALIDATION):
        machine = TaskStateMachine()
        machine.start("задача")
        if phase != TaskPhase.PLANNING:
            machine.set_plan_confirmed(True)
            machine.to_execution()
        if phase == TaskPhase.VALIDATION:
            machine.to_validation()
        machine.pause()
        assert machine.state.paused
        assert machine.state.phase == phase  # пауза не меняет фазу
        machine.resume()
        assert not machine.state.paused


def test_resume_preserves_expected_action() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"])
    machine.set_plan_confirmed(True)
    machine.to_execution("протестировать модуль")
    machine.pause()
    assert machine.state.expected_action == "протестировать модуль"
    machine.resume()
    assert machine.state.expected_action == "протестировать модуль"


def test_set_expected_action() -> None:
    machine = TaskStateMachine()
    machine.start("задача")
    machine.set_expected_action("сделать X")
    assert machine.state.expected_action == "сделать X"


def test_reset_returns_to_idle() -> None:
    machine = TaskStateMachine()
    machine.start("задача")
    assert machine.state.is_active
    machine.reset()
    assert machine.state.phase == TaskPhase.IDLE
    assert not machine.state.is_active


def test_serialization_roundtrip() -> None:
    machine = TaskStateMachine()
    machine.start("описание", ["а", "б", "в"])
    machine.set_plan_confirmed(True)
    machine.to_execution("сделать")
    machine.advance_step()
    machine.pause()
    state = machine.state
    restored = TaskState.from_dict(state.to_dict())
    assert restored == state
    assert restored.phase == TaskPhase.EXECUTION
    assert restored.description == "описание"
    assert restored.steps == ["а", "б", "в"]
    assert restored.expected_action == "сделать"
    assert restored.paused is True


def test_from_dict_empty_or_garbage_is_idle() -> None:
    assert TaskState.from_dict(None).phase == TaskPhase.IDLE
    assert TaskState.from_dict({}).phase == TaskPhase.IDLE
    # повреждённая фаза не роняет восстановление
    broken = TaskState.from_dict({"phase": "nope", "steps": ["x"], "step": "not-int", "paused": 1})
    assert broken.phase == TaskPhase.IDLE
    assert broken.is_active is False


def test_phase_labels_cover_all_phases() -> None:
    for phase in TaskPhase:
        assert phase.value in PHASE_LABELS


def test_planning_to_execution_requires_confirmation() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["шаг"])
    assert machine.state.plan_confirmed is False
    # без подтверждения перейти в выполнение нельзя
    with pytest.raises(InvalidTaskTransition) as exc:
        machine.to_execution()
    assert "план не подтверждён" in str(exc.value)
    machine.set_plan_confirmed(True)
    assert machine.state.plan_confirmed
    machine.to_execution()
    assert machine.state.phase == TaskPhase.EXECUTION


def test_validation_can_return_to_planning() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["шаг"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.to_validation()
    # при корректировке плана возвращаемся в планирование
    machine.to_planning()
    assert machine.state.phase == TaskPhase.PLANNING
    # вернулись в планирование — подтверждение сбрасывается
    assert machine.state.plan_confirmed is False


def test_set_plan_replaces_steps_and_resets_confirmation() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а", "б"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.to_planning()
    machine.set_plan(["новый шаг"], description="обновлённый план")
    assert machine.state.steps == ["новый шаг"]
    assert machine.state.step == 1
    assert machine.state.description == "обновлённый план"
    assert machine.state.plan_confirmed is False


def test_set_plan_confirmed_flow() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"])
    assert not machine.state.plan_confirmed
    machine.set_plan_confirmed(True)
    assert machine.state.plan_confirmed
    machine.set_plan_confirmed(False)
    assert not machine.state.plan_confirmed
    with pytest.raises(InvalidTaskTransition):
        machine.to_execution()


def test_validation_phase_uses_validation_steps() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["произвести"], validation_steps=["проверить"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    assert machine.state.active_steps() == ["произвести"]
    assert machine.state.step == 1
    machine.to_validation()
    # при переходе в проверку счётчик сбрасывается, активен список проверки
    assert machine.state.phase == TaskPhase.VALIDATION
    assert machine.state.active_steps() == ["проверить"]
    assert machine.state.step == 1


def test_step_text_uses_active_list() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а", "б"], validation_steps=["в"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.advance_step()
    # выполнили последний шаг выполнения — авто-переход в проверку,
    # активный список переключается на шаги проверки
    assert machine.state.phase == TaskPhase.VALIDATION
    assert machine.state.step_text == "в"


def test_advance_requires_work_phase() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"])
    with pytest.raises(InvalidTaskTransition):
        machine.advance_step()


def test_advance_with_done_false_keeps_step() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а", "б"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.advance_step(done=False, expected_action="ещё работать")
    assert machine.state.step == 1
    assert machine.state.expected_action == "ещё работать"


def test_describe_lists_both_step_sections() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["произвести"], validation_steps=["проверить"])
    text = machine.describe()
    assert "Шаги выполнения" in text
    assert "Шаги проверки" in text


def test_serialization_includes_validation_steps() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"], validation_steps=["в"])
    restored = TaskState.from_dict(machine.state.to_dict())
    assert restored == machine.state
    assert restored.validation_steps == ["в"]


def test_advance_at_end_transitions_to_validation() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.advance_step()
    # последний шаг выполнения — авто-переход в проверку (а не подсказка)
    assert machine.state.phase == TaskPhase.VALIDATION
    assert "проверь" in machine.state.expected_action


def test_advance_at_validation_end_transitions_to_done() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"], validation_steps=["в"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    machine.advance_step()  # выполнение → проверка (шаг 1 из 1)
    assert machine.state.phase == TaskPhase.VALIDATION
    assert machine.state.step == 1
    machine.advance_step()  # последний шаг проверки → готово
    assert machine.state.phase == TaskPhase.DONE
    assert "отчёт" in machine.state.expected_action


def test_self_transition_is_noop() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["а"])
    machine.set_plan_confirmed(True)
    machine.to_execution()
    # повторный set_phase(execution) после confirm — no-op, а не ошибка
    machine.to_execution()
    assert machine.state.phase == TaskPhase.EXECUTION
