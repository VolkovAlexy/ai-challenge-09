"""LongTermMemory — задел под долгосрочную память (в v1 stub).

Контракт зафиксирован: `recall(query)` возвращает релевантные чанки,
`store(messages)` — закрепляет важные фрагменты диалога. Интеграционная
точка — `ContextBuilder.build_messages(..., memories=...)`.

Открытый вопрос (решается при реализации): память per-agent или общая
на процесс/профиля пользователя.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from my_agent.core.message import Message


class Chunk:
    """Чанк памяти/знаний: текст + метаданные для ранжирования."""

    def __init__(self, text: str, metadata: dict[str, str] | None = None) -> None:
        self.text = text
        self.metadata = metadata or {}


@runtime_checkable
class LongTermMemory(Protocol):
    """Протокол долгосрочной памяти агента."""

    async def recall(self, query: str) -> list[Chunk]:
        """Найти релевантные воспоминания под запрос."""
        ...

    async def store(self, messages: list[Message]) -> None:
        """Сохранить фрагменты диалога в долгосрочную память."""
        ...


class InMemoryLongTermMemory:
    """Stub-реализация протокола: не хранит ничего, recall возвращает []."""

    async def recall(self, query: str) -> list[Chunk]:
        """Stub: долгосрочная память в v1 пуста."""
        return []

    async def store(self, messages: list[Message]) -> None:
        """Stub: ничего не сохраняет."""
        return None
