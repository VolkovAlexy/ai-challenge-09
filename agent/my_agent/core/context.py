"""ContextBuilder — единственная точка, где собирается массив messages.

Сейчас: системный промпт + (саммари сжатой истории) + история. RAG-чанки и
долгосрочные памяти — задел: при передаче `rag_chunks`/`memories` они
добавляются контекстом до истории; при реализации эти ветки не потребуют
изменений в agent.py.

Здесь же — общая оценка токенов (chars/4), используемая агентом и
компактором как фолбэк, когда API не вернул usage.
"""

from __future__ import annotations

import json

from my_agent.core.message import Message, Role

RAG_HEADER = "Контекст из внешних источников (RAG):"
MEMORY_HEADER = "Долгосрочные воспоминания:"
SUMMARY_HEADER = "Сводка ранее в диалоге:"

CHARS_PER_TOKEN = 4  # грубая оценка: для русского занижает в ~1.5–2 раза


def estimate_text(text: str) -> int:
    """Грубая оценка токенов текста: ~4 символа на токен."""
    return len(text) // CHARS_PER_TOKEN


def estimate_messages(messages: list[Message]) -> int:
    """Оценка контекста запроса по сериализованным сообщениям."""
    return sum(
        len(json.dumps(m.to_api(), ensure_ascii=False)) // CHARS_PER_TOKEN for m in messages
    )


class ContextBuilder:
    """Собирает финальный массив сообщений для запроса к LLM."""

    def build_messages(
        self,
        system_prompt: str,
        history: list[Message],
        summary: str | None = None,
        rag_chunks: list[str] | None = None,
        memories: list[str] | None = None,
    ) -> list[Message]:
        """system_prompt → (+саммари, +RAG, +memories как служебные сообщения) → история."""
        messages = [Message(role=Role.SYSTEM, content=system_prompt)]
        if summary:
            messages.append(Message(role=Role.SYSTEM, content=SUMMARY_HEADER + "\n" + summary))
        if rag_chunks:
            content = RAG_HEADER + "\n" + "\n\n".join(rag_chunks)
            messages.append(Message(role=Role.SYSTEM, content=content))
        if memories:
            content = MEMORY_HEADER + "\n" + "\n".join(memories)
            messages.append(Message(role=Role.SYSTEM, content=content))
        messages.extend(history)
        return messages
