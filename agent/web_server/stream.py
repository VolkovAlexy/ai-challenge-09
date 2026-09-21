"""SSE-стриминг хода агента поверх Agent.start_ask().

Бэкенд не читает чанки API напрямую — он поллит публичное состояние агента
(streaming_text, is_compacting, compaction_note) и отдаёт дельты клиенту.
Один pump-генератор на запрос; отключение SSE-клиента не отменяет ход
агента — он продолжается в ядре, результат записывает автосохранение.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any

from agent.core.agent import AgentBusyError
from agent.core.message import Message, Role
from agent.llm.client import LLMError
from agent.web_server.state import AgentRecord, WebState

POLL_INTERVAL = 0.1  # поллинг состояния агента (~100 мс)

_COMPACTION_RE = re.compile(
    r"Контекст сжат: -(?P<removed>[\d.]+[kM]?) удалено, "
    r"\+(?P<summary>[\d.]+[kM]?) саммари \((?P<before>\d+)% → (?P<after>\d+)%\)"
)

# предложение памяти: в дельты не уходит, доходит отдельным событием memory_suggestion
_SUG_START = "[MEMORY_SUGGESTION]"
_SUG_END = "[/MEMORY_SUGGESTION]"


def _partial_marker_len(text: str, marker: str) -> int:
    """Длина хвоста text, который может быть началом marker (удерживаем от отправки)."""
    for size in range(min(len(marker) - 1, len(text)), 0, -1):
        if marker.startswith(text[-size:]):
            return size
    return 0


def _visible_text(full: str) -> str:
    """Стрим-текст без MEMORY_SUGGESTION-блока.

    Хвост-кандидат на неполный старт-маркер удерживается (пришлём позже,
    когда выяснится, маркер это или нет); от старт-маркера до конца
    энмаркер-блока текст не отправляется вовсе.
    """
    start = full.find(_SUG_START)
    if start == -1:
        hold = _partial_marker_len(full, _SUG_START)
        return full[: len(full) - hold] if hold else full
    end = full.find(_SUG_END, start)
    if end == -1:
        return full[:start]
    return full[:start] + full[end + len(_SUG_END) :]


def _parse_fmt(text: str) -> int:
    text = text.strip()
    if text.endswith("k"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("M"):
        return int(float(text[:-1]) * 1000_000)
    return int(text)


def _compaction_payload(note: str | None) -> dict[str, Any] | None:
    """Разбирает compaction_note в поля события compaction_done; None — парс не удался."""
    if not note:
        return None
    match = _COMPACTION_RE.search(note)
    if match is None:
        return None
    return {
        "removed": _parse_fmt(match.group("removed")),
        "summary_tokens": _parse_fmt(match.group("summary")),
        "pct_before": int(match.group("before")) / 100.0,
        "pct_after": int(match.group("after")) / 100.0,
    }


def _sse(event: str, payload: dict[str, Any]) -> dict[str, str]:
    """Событие SSE: event-имя лежит ВНУТРИ data-JSON.

    Фронтовый парсер (web/src/api/sse.ts) читает только `data:` строки и
    ожидает, что JSON в них содержит поле `event`. ПОЛЕ `event:` в SSE
    фронт игнорирует, поэтому нельзя его использовать.
    """
    return {"data": json.dumps({"event": event, **payload}, ensure_ascii=False)}


def _error_kind(exc: LLMError) -> str:
    """http — сервер ответил 4xx/5xx; network — сетевые/таймаут errors."""
    status = exc.status
    if status is not None and 400 <= status < 600:
        return "http"
    return "network"


async def agent_stream(
    state: WebState,
    record: AgentRecord,
    content: str,
) -> AsyncIterator[dict[str, str]]:
    """Полный SSE-ход: user_message → (compaction_*) → (tool_message|scratchpad)*
    → delta* → done → (memory_suggestion); терминалы: done/cancelled/error."""
    agent = record.agent
    user_index = len(agent.memory.history)
    user_msg = state.message_dto(
        Message(role=Role.USER, content=content), user_index
    )
    yield _sse("user_message", {"message": user_msg.model_dump()})

    try:
        task = agent.start_ask(content)
    except AgentBusyError as exc:
        yield _sse("error", {"kind": "http", "detail": str(exc)})
        return

    sent = ""
    compaction_active = False
    scratchpad_sent = agent.memory.scratchpad

    async def drain_tool_events() -> AsyncIterator[dict[str, str]]:
        """Артефакты tool-раундов: сообщения + изменившийся scratchpad."""
        nonlocal scratchpad_sent
        while agent.turn_events:
            idx, msg = agent.turn_events.pop(0)
            yield _sse("tool_message", {"message": state.message_dto(msg, idx).model_dump()})
        if agent.memory.scratchpad != scratchpad_sent:
            scratchpad_sent = agent.memory.scratchpad
            yield _sse("scratchpad", {"content": agent.memory.scratchpad})

    try:
        while not task.done():
            if agent.is_compacting and not compaction_active:
                yield _sse("compaction_started", {})
                compaction_active = True
            elif compaction_active and not agent.is_compacting:
                payload = _compaction_payload(agent.compaction_note)
                if payload is not None:
                    yield _sse("compaction_done", payload)
                compaction_active = False
            async for event in drain_tool_events():
                yield event
            full = _visible_text(agent.streaming_text)
            if len(full) < len(sent):
                # новый раунд после инструментов: стрим начался заново
                sent = ""
            if len(full) > len(sent):
                yield _sse("delta", {"content": full[len(sent) :]})
                sent = full
            await asyncio.sleep(POLL_INTERVAL)

        # финальный дрен: tool-артефакты конца хода уходят до done
        async for event in drain_tool_events():
            yield event

        if task.cancelled():
            yield _sse("cancelled", {})
            return
        try:
            task.result()
        except asyncio.CancelledError:
            yield _sse("cancelled", {})
        except LLMError as exc:
            yield _sse("error", {"kind": _error_kind(exc), "detail": str(exc)})
        except Exception as exc:
            yield _sse("error", {"kind": "network", "detail": f"Внутренняя ошибка: {exc}"})
        else:
            full = agent.memory.history[-1].content or ""
            if len(full) > len(sent):  # дозакрываем последний дельт-хвост до done
                yield _sse("delta", {"content": full[len(sent) :]})
                sent = full
            message = agent.memory.history[-1]
            idx = len(agent.memory.history) - 1
            usage = state.usage_dto(agent.last_usage)
            yield _sse(
                "done",
                {"message": state.message_dto(message, idx, usage).model_dump()},
            )
            suggestion = agent.pending_memory_suggestion
            if suggestion:
                yield _sse(
                    "memory_suggestion",
                    {"content": suggestion},
                )
    finally:
        state.persist(record)
