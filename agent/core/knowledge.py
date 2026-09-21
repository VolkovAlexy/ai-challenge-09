"""KnowledgeBase — задел под RAG (в v1 stub).

Интеграционная точка: `ContextBuilder.build_messages(..., rag_chunks=...)` —
`Agent.ask()` будет передавать сюда результат `search(query)` для текущего
запроса пользователя.
"""

from __future__ import annotations

from agent.memory.longterm import Chunk


class KnowledgeBase:
    """Поиск знаний по запросу (stub в v1: не находит ничего)."""

    async def search(self, query: str) -> list[Chunk]:
        """Stub: RAG-индекс в v1 отсутствует."""
        return []
