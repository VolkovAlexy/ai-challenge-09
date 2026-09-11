"""ContextCompactor: сжатие префикса истории диалога в саммари.

Один LLM-вызов на сжатие: старейший префикс несжатой истории (+ прежнее
саммари, если было) заменяется новым саммари. Дословно остаётся хвост,
влезающий в `COMPACT_TARGET_SHARE` окна. Суммаризация — той же моделью,
что и чат. Сам компактор историю не удаляет — чат остаётся полным, сжатие
меняет только проекцию для LLM (см. Agent._projection).
"""

from __future__ import annotations

from dataclasses import dataclass

from my_agent.core.context import estimate_messages, estimate_text
from my_agent.core.message import ChatRequest, Message, Role, Usage
from my_agent.llm.client import LLMClient, LLMError

SUMMARY_SYSTEM_PROMPT = (
    "Ты — ассистент для сжатия истории диалога. Кратко и структурно перескажай "
    "диалог, сохранив: задачи и цели пользователя, ключевые факты, решения, "
    "важные фрагменты кода и команды, незавершённые шаги. Отвечай на языке "
    "диалога. Ничего от себя не добавляй."
)

# после сжатия саммари + дословный хвост занимают не больше этой доли окна
COMPACT_TARGET_SHARE = 0.4

# минимум дословных сообщений (пара user+assistant), которые не сжимаем
MIN_KEEP = 2

# запас на обёртку саммари в промпте (заголовок + JSON-обвязка сообщения)
_SUMMARY_SLACK_TOKENS = 64

# потолок выхода суммаризатора: thinking-моделям нужен запас на размышления
# (max_tokens у провайдера считается целиком, включая reasoning), но лимит
# не должен пробивать COMPACT_TARGET_SHARE на малых окнах
_SUMMARY_MAX_TOKENS_CAP = 4096
_SUMMARY_WINDOW_SHARE = 0.25
_SUMMARY_MIN_TOKENS = 512


@dataclass
class CompactionResult:
    """Итог сжатия: новое саммари + сколько сообщений префикса покрыто саммари.

    `removed_tokens` — оценка веса исчезнувшей из проекции части: удалённый
    префикс + прежнее саммари, которое он заменяет (масштабируется
    коэффициентом токенизатора ratio, т.к. chars/4 для русского занижает).
    in_tokens/out_tokens — расход LLM-вызова суммаризации (точные числа
    от API либо локальная оценка chars/4); попадают в накопительные
    счётчики сессии (Agent.totals).
    """

    summary: str
    removed: int
    removed_tokens: int = 0
    in_tokens: int = 0
    out_tokens: int = 0
    estimated: bool = True


class ContextCompactor:
    """Планирует и выполняет сжатие истории (plain Python, без UI)."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def plan_removal(
        self,
        history: list[Message],
        window: int,
        *,
        system_prompt: str,
        previous_summary: str,
        ratio: float = 1.0,
    ) -> int:
        """Сколько старейших сообщений удалить: хвост влезает в долю окна.

        Учитывает накладные расходы (системный промпт, сообщение-саммари);
        всегда оставляет минимум MIN_KEEP сообщений. 0 — сжимать нечего.
        `ratio` — наблюдаемый коэффициент токенизатора (API / chars/4):
        локальная оценка занижает, поэтому все оценки масштабируются ею.
        """
        budget = (
            int(window * COMPACT_TARGET_SHARE)
            - int(estimate_text(system_prompt) * ratio)
            - int(estimate_text(previous_summary) * ratio if previous_summary else 0)
            - _SUMMARY_SLACK_TOKENS
        )
        acc = 0
        keep = 0
        for message in reversed(history):
            size = int(estimate_messages([message]) * ratio)
            if keep >= MIN_KEEP and acc + size > budget:
                break
            acc += size
            keep += 1
        return len(history) - keep

    async def compact(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        previous_summary: str,
        history: list[Message],
        window: int,
        system_prompt: str,
        ratio: float = 1.0,
    ) -> CompactionResult | None:
        """Сжимает историю; None — сжимать нечего (порог формально превышен)."""
        removed = self.plan_removal(
            history,
            window,
            system_prompt=system_prompt,
            previous_summary=previous_summary,
            ratio=ratio,
        )
        if removed <= 0:
            return None
        result = await self._summarize(
            removed=removed,
            model=model,
            api_base=api_base,
            api_key=api_key,
            previous_summary=previous_summary,
            messages=history[:removed],
            window=window,
        )
        # вес исчезнувшей из проекции части: прежнее саммари + удалённый префикс
        # (в оценках токенизатора API — локальная chars/4 занижает)
        result.removed_tokens = int(
            (
                (estimate_text(previous_summary) if previous_summary else 0)
                + estimate_messages(history[:removed])
            )
            * ratio
        )
        return result

    async def _summarize(
        self,
        *,
        removed: int,
        model: str,
        api_base: str,
        api_key: str,
        previous_summary: str,
        messages: list[Message],
        window: int,
    ) -> CompactionResult:
        """Один запрос к LLM: предыдущая сводка + удаляемый префикс → новое саммари.

        Расход вызова: точный (usage в стриме) либо оценка chars/4 — в результате.
        """
        body = self._transcript(messages)
        if previous_summary:
            body = (
                f"Предыдущая сводка:\n{previous_summary}\n\n"
                f"Продолжение диалога:\n{body}\n\n"
                "Объедини предыдущую сводку и продолжение в одну обновлённую сводку."
            )
        else:
            body = body + "\n\nНапиши сводку диалога."
        max_tokens = min(
            _SUMMARY_MAX_TOKENS_CAP,
            max(_SUMMARY_MIN_TOKENS, int(window * _SUMMARY_WINDOW_SHARE)),
        )
        request = ChatRequest(
            model=model,
            messages=[
                Message(role=Role.SYSTEM, content=SUMMARY_SYSTEM_PROMPT),
                Message(role=Role.USER, content=body),
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        text = ""
        reasoning_text = ""
        finish_reason: str | None = None
        server_usage: Usage | None = None
        async for chunk in self._llm.astream(request, api_base, api_key):
            if chunk.usage is not None:
                server_usage = chunk.usage
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.content:
                text += chunk.content
            if chunk.reasoning:
                reasoning_text += chunk.reasoning
        text = text.strip()
        if not text:
            if finish_reason == "length":
                raise LLMError(
                    f"суммаризатор исчерпал лимит {max_tokens} токенов (возможно, "
                    "thinking-модель потратила его на размышления), саммари не получено"
                )
            raise LLMError("суммаризатор вернул пустой ответ")
        if server_usage is not None:
            result = CompactionResult(
                summary=text,
                removed=removed,
                in_tokens=server_usage.prompt_tokens,
                out_tokens=server_usage.completion_tokens,
                estimated=False,
            )
        else:
            # thinking-суммаризатор тоже тратит токены на размышления
            result = CompactionResult(
                summary=text,
                removed=removed,
                in_tokens=estimate_messages(request.messages),
                out_tokens=estimate_text(text) + estimate_text(reasoning_text),
            )
        return result

    @staticmethod
    def _transcript(messages: list[Message]) -> str:
        """История в виде «роль: текст» (tool_calls — перечислением имён)."""
        lines: list[str] = []
        for message in messages:
            content = message.content or ""
            if message.tool_calls:
                names = ", ".join(tc.function.name or "?" for tc in message.tool_calls)
                content = (content + "\n" if content else "") + f"[tool_calls: {names}]"
            lines.append(f"{message.role.value}: {content}")
        return "\n\n".join(lines)
