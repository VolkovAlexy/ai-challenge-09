"""Фоновый «сторож»: периодически отправляет агенту промпт (24/7-сценарий).

Каждый тик сторож выбирает целевого агента (по `agent_id` из конфига либо
активный) и, если тот не занят, запускает `start_ask(prompt)`. После завершения
хода снапшот сессии персистится (автосейв тоже это делает, но здесь — сразу).
"""

from __future__ import annotations

import asyncio
import contextlib

from agent.config.schema import WatchdogConfig
from agent.core.agent import AgentBusyError
from agent.web_server.state import AgentRecord, WebState


def resolve_target(state: WebState, cfg: WatchdogConfig) -> AgentRecord | None:
    """Целевой агент сторожа: по `agent_id` либо активный (None — не определился)."""
    if cfg.agent_id is not None:
        return state.get(cfg.agent_id)
    return state.active_agent()


async def watchdog_tick(state: WebState, cfg: WatchdogConfig) -> bool:
    """Один тик: запускает ход агенту, если он свободен. True — ход запущен.

    Агент не найден, уже отвечает или `start_ask` бросил `AgentBusyError` —
    False (пропуск). После хода — `persist` сессии.
    """
    record = resolve_target(state, cfg)
    if record is None or record.agent.is_streaming:
        return False
    try:
        task = record.agent.start_ask(cfg.prompt)
    except AgentBusyError:
        return False
    with contextlib.suppress(asyncio.CancelledError):
        await task
    state.persist(record)
    return True


async def watchdog_loop(state: WebState, cfg: WatchdogConfig) -> None:
    """Цикл сторожа: тик каждые `interval_seconds`, пока сторож не остановлен."""
    while True:
        await asyncio.sleep(cfg.interval_seconds)
        await watchdog_tick(state, cfg)
