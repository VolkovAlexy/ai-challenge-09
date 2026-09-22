"""ContextBuilder — единственная точка, где собирается массив messages.

Сейчас: системный промпт + (facts, саммари сжатой истории) + история
(для стратегий sliding/facts история урезается скользящим окном).
RAG-чанки и долгосрочные памяти — задел: при передаче `rag_chunks`/`memories`
они добавляются контекстом до истории; при реализации эти ветки не потребуют
изменений в agent.py.

Здесь же — общая оценка токенов (chars/4), используемая агентом и
компактором как фолбэк, когда API не вернул usage.
"""

from __future__ import annotations

import json

from agent.core.message import Message, Role
from agent.core.task import PHASE_PROTOCOL, TaskState
from agent.memory.longterm import LONGTERM_HEADER, LONGTERM_INSTRUCTION

RAG_HEADER = "Контекст из внешних источников (RAG):"
MEMORY_HEADER = "Долгосрочные воспоминания:"
SUMMARY_HEADER = "Сводка ранее в диалоге:"
FACTS_HEADER = "Важные факты диалога (ключ: значение):"
SCRATCHPAD_HEADER = "Рабочая память (scratchpad — заметки по текущей задаче):"
INVARIANTS_HEADER = "Ограничения (инварианты — обязательные требования к результату):"
TASK_HEADER = "Активная задача (конечный автомат):"

CHARS_PER_TOKEN = 4  # грубая оценка: для русского занижает в ~1.5–2 раза


def estimate_text(text: str) -> int:
    """Грубая оценка токенов текста: ~4 символа на токен."""
    return len(text) // CHARS_PER_TOKEN


def estimate_messages(messages: list[Message]) -> int:
    """Оценка контекста запроса по сериализованным сообщениям."""
    return sum(
        len(json.dumps(m.to_api(), ensure_ascii=False)) // CHARS_PER_TOKEN for m in messages
    )


def fmt_tokens(n: int) -> str:
    """Компактный формат: 456 / 2039 / 12.3k / 1.5M (до 10k — точное число)."""
    if n < 10_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def apply_sliding_window(history: list[Message], n: int) -> list[Message]:
    """Стратегия sliding window: только последние n сообщений.

    Полная история агента не меняется — окно режет только проекцию для LLM.
    """
    if n <= 0 or len(history) <= n:
        return list(history)
    return list(history[-n:])


class ContextBuilder:
    """Собирает финальный массив сообщений для запроса к LLM."""

    def build_messages(
        self,
        system_prompt: str,
        history: list[Message],
        summary: str | None = None,
        facts: dict[str, str] | None = None,
        rag_chunks: list[str] | None = None,
        memories: list[str] | None = None,
        longterm: str | None = None,
        scratchpad: str | None = None,
        invariants: list[str] | None = None,
        task: TaskState | None = None,
    ) -> list[Message]:
        """system_prompt → (+facts, +саммари, +RAG, +memories, +longterm,
        +scratchpad, +ограничения, +задача служебными сообщениями) → история.

        `longterm` — содержимое долговременной памяти (markdown-файл, общий
        для всех агентов); идёт сразу после системного промпта вместе с
        инструкцией предлагать новые знания через [MEMORY_SUGGESTION].
        `scratchpad` — рабочая память текущей задачи (заметки агента).
        `invariants` — ограничения (инварианты) сессии: обязательные
        требования к результату, по которым проверяется работа на этапе
        проверки.
        `task` — состояние задачи как конечный автомат: добавляется только
        при активной задаче, чтобы модель каждый ход видела этап/шаг/
        ожидаемое действие (продолжение без повторных объяснений).
        """
        messages = [Message(role=Role.SYSTEM, content=system_prompt)]
        if longterm:
            content = (
                LONGTERM_HEADER
                + "\n"
                + longterm.strip()
                + "\n\n"
                + LONGTERM_INSTRUCTION
            )
            messages.append(Message(role=Role.SYSTEM, content=content))
        if scratchpad:
            messages.append(
                Message(role=Role.SYSTEM, content=SCRATCHPAD_HEADER + "\n" + scratchpad)
            )
        if invariants:
            lines = "\n".join(f"- {text}" for text in invariants)
            messages.append(
                Message(role=Role.SYSTEM, content=INVARIANTS_HEADER + "\n" + lines)
            )
        if task is not None and task.is_active:
            messages.append(
                Message(role=Role.SYSTEM, content=TASK_HEADER + "\n" + task.describe())
            )
            messages.append(Message(role=Role.SYSTEM, content=PHASE_PROTOCOL))
        if facts:
            lines = "\n".join(f"- {key}: {value}" for key, value in sorted(facts.items()))
            messages.append(Message(role=Role.SYSTEM, content=FACTS_HEADER + "\n" + lines))
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
