"""ToolContext — контекст запуска инструмента (сессия, агент, запуск).

Передаётся в `Tool.execute(arguments, ctx)`, чтобы динамические инструменты
(например, MCP) могли обращаться к состоянию запуска без привязки к сессии
в конструкторе. Для статичных инструментов (task/scratchpad/invariants,
делегирование) контекст фактически дублирует уже внедрённые зависимости.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agent.memory.session import InMemorySession

if TYPE_CHECKING:
    from agent.core.agent import Agent


@dataclass
class ToolContext:
    """Контекст запуска инструмента: сессия, агент, id запуска."""

    session: InMemorySession
    agent: Agent
    project_id: str
    run_id: str
