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
    machine.to_execution()
    machine.to_validation()
    # нашли баг — возврат на выполнение
    machine.to_execution("исправить")
    assert machine.state.phase == TaskPhase.EXECUTION


def test_advance_step_clamps_to_plan_length() -> None:
    machine = TaskStateMachine()
    machine.start("задача", ["шаг1", "шаг2"])
    machine.to_execution()
    machine.advance_step()
    assert machine.state.step == 2
    machine.advance_step()
    assert machine.state.step == 2  # не превышает len(steps)


def test_pause_any_phase_and_resume() -> None:
    for phase in (TaskPhase.PLANNING, TaskPhase.EXECUTION, TaskPhase.VALIDATION):
        machine = TaskStateMachine()
        machine.start("задача")
        if phase != TaskPhase.PLANNING:
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
    machine.start("описание", ["а", "б"])
    machine.to_execution("сделать")
    machine.advance_step()
    machine.pause()
    state = machine.state
    restored = TaskState.from_dict(state.to_dict())
    assert restored == state
    assert restored.phase == TaskPhase.EXECUTION
    assert restored.description == "описание"
    assert restored.steps == ["а", "б"]
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
