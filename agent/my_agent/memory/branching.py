"""Ветки диалога (branching): checkpoint + независимое продолжение.

Ветка — именованный снапшот состояния диалога (история + саммари + его
длина + facts). Активная ветка хранится в полях `InMemorySession` как
обычно; неактивные — в словаре `branches`. `fork()` сохраняет чекпоинт
текущего диалога в новую ветку и переключается на неё; `switch()`
возвращается к сохранённой ветке. Во время стрима переключение запрещено
(см. Agent.fork_branch / Agent.switch_branch).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from my_agent.core.message import Message

DEFAULT_BRANCH = "main"


class BranchError(Exception):
    """Ошибка операций над ветками (нет ветки, имя занято)."""


@dataclass
class BranchState:
    """Полное состояние одной ветки диалога."""

    history: list[Message] = field(default_factory=list)
    summary: str | None = None
    compacted_upto: int = 0
    facts: dict[str, str] = field(default_factory=dict)


def normalize_branch_name(raw: str) -> str:
    """Валидирует имя ветки: непустое, без пробелов, в нижний регистр."""
    name = raw.strip()
    if not name or any(ch.isspace() for ch in name):
        raise BranchError(
            f"неверное имя ветки '{raw}': имя должно быть непустым и без пробелов"
        )
    return name.lower()
