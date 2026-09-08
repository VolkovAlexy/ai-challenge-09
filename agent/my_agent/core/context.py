"""ContextBuilder — единственная точка, где собирается массив messages.

Сейчас: системный промпт + история. RAG-чанки и долгосрочные памяти —
задел: при передаче `rag_chunks`/`memories` они добавляются контекстом
до истории; при реализации эти ветки не потребуют изменений в agent.py.
"""

from __future__ import annotations

from my_agent.core.message import Message, Role

RAG_HEADER = "Контекст из внешних источников (RAG):"
MEMORY_HEADER = "Долгосрочные воспоминания:"


class ContextBuilder:
    """Собирает финальный массив сообщений для запроса к LLM."""

    def build_messages(
        self,
        system_prompt: str,
        history: list[Message],
        rag_chunks: list[str] | None = None,
        memories: list[str] | None = None,
    ) -> list[Message]:
        """system_prompt → (+RAG, +memories как служебные контекстные сообщения) → история."""
        messages = [Message(role=Role.SYSTEM, content=system_prompt)]
        if rag_chunks:
            content = RAG_HEADER + "\n" + "\n\n".join(rag_chunks)
            messages.append(Message(role=Role.SYSTEM, content=content))
        if memories:
            content = MEMORY_HEADER + "\n" + "\n".join(memories)
            messages.append(Message(role=Role.SYSTEM, content=content))
        messages.extend(history)
        return messages
