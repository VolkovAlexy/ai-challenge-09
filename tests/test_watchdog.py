"""Тесты фонового сторожа (24/7-сценарий): периодический запуск хода агента."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.config.schema import WatchdogConfig
from agent.core.message import ChatChunk, Role
from agent.web_server.state import WebState
from agent.web_server.watchdog import resolve_target, watchdog_tick
from tests.test_web_server import MockLLM, make_config

PROMPT = "Сформируй отчёт"


def make_state() -> WebState:
    llm = MockLLM([ChatChunk(content="отчёт готов"), ChatChunk(finish_reason="stop")])
    from agent.memory.persistence import SessionStore
    from agent.tools.registry import ToolRegistry

    return WebState(
        config=make_config(),
        llm=llm,  # type: ignore[arg-type]
        tools=ToolRegistry(),
        store=SessionStore(Path(":memory:")),
        default_system_prompt="SP",
    )


async def test_resolve_target_active() -> None:
    state = make_state()
    agent = state.create_agent(name="a")
    assert resolve_target(state, WatchdogConfig(prompt=PROMPT)) is agent


async def test_resolve_target_by_id() -> None:
    state = make_state()
    agent = state.create_agent(name="a")
    state.create_agent(name="b")
    cfg = WatchdogConfig(prompt=PROMPT, agent_id=agent.agent_id)
    assert resolve_target(state, cfg) is agent
    # активный агент — первый созданный
    assert resolve_target(state, WatchdogConfig(prompt=PROMPT)) is agent


async def test_watchdog_tick_runs_prompt() -> None:
    state = make_state()
    agent = state.create_agent(name="a")
    cfg = WatchdogConfig(prompt=PROMPT, interval_seconds=0.01)
    assert await watchdog_tick(state, cfg) is True
    roles = [m.role for m in agent.agent.memory.history]
    assert roles == [Role.USER, Role.ASSISTANT]
    assert agent.agent.memory.history[0].content == PROMPT


async def test_watchdog_tick_skips_busy_agent() -> None:
    state = make_state()
    agent = state.create_agent(name="a")
    task = agent.agent.start_ask(PROMPT)
    cfg = WatchdogConfig(prompt=PROMPT, interval_seconds=0.01)
    assert await watchdog_tick(state, cfg) is False
    await task


async def test_watchdog_tick_skips_no_target() -> None:
    state = make_state()
    cfg = WatchdogConfig(prompt=PROMPT, interval_seconds=0.01)
    assert await watchdog_tick(state, cfg) is False


async def test_watchdog_tick_skips_unknown_agent_id() -> None:
    state = make_state()
    state.create_agent(name="a")
    cfg = WatchdogConfig(prompt=PROMPT, agent_id="nope", interval_seconds=0.01)
    assert await watchdog_tick(state, cfg) is False


@pytest.mark.parametrize(
    "enabled,interval",
    [(False, 1.0), (True, 0.01)],
)
def test_watchdog_config_validation(enabled: bool, interval: float) -> None:
    cfg = WatchdogConfig(
        enabled=enabled, interval_seconds=interval, prompt=PROMPT
    )
    assert cfg.enabled is enabled
    assert cfg.interval_seconds == interval


def test_watchdog_config_invalid_interval() -> None:
    with pytest.raises(ValueError):
        WatchdogConfig(prompt=PROMPT, interval_seconds=0)
