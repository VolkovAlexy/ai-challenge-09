"""Sticky Facts: ключ-значение память диалога (стратегия facts).

`facts` — обычный dict в состоянии агента (хранится в сессии и ветках,
см. InMemorySession). FactsExtractor обновляет его одним LLM-вызовом после
каждого сообщения пользователя: модели отдаются текущие facts + недавний
хвост диалога, она возвращает обновлённый JSON-объект (добавить/обновить/
удалить ключ = не включить его в ответ). При ошибке запроса или не-JSON
ответе пробрасывается LLMError — агент оставляет старые facts и продолжает
чат. В проекцию facts попадают system-сообщением (ContextBuilder, FACTS_HEADER).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agent.core.context import estimate_messages, estimate_text
from agent.core.message import ChatRequest, Message, Role, Usage
from agent.llm.client import LLMClient, LLMError

FACTS_EXTRACTOR_PROMPT = (
    "Ты — ассистент, который ведёт память диалога в формате ключ-значение. "
    "Тебе даны текущие факты и последние сообщения диалога. Обнови факты: добавь "
    "новое, обнови изменившееся, удали устаревшее (не включай его в ответ). "
    "Храни: цель пользователя, ограничения, предпочтения, принятые решения, "
    "договорённости, важные технические детали (пути, версии, модели, настройки). "
    "Ключи — короткие (1–3 слова, snake_case, можно по-русски), значения — краткие формулировки. "
    "Отвечай ТОЛЬКО валидным JSON-объектом вида {\"ключ\": \"значение\"} без markdown "
    "и пояснений. Если обновлять нечего — верни текущий объект без изменений."
)

# потолок вывода извлекателя: thinking-моделям нужен запас на размышления
FACTS_MAX_TOKENS = 1024

# сколько последних сообщений диалога даём модели для контекста
FACTS_CONTEXT_MESSAGES = 6


@dataclass
class FactsResult:
    """Итог обновления facts + расход LLM-вызова (для Agent.totals)."""

    facts: dict[str, str] = field(default_factory=dict)
    in_tokens: int = 0
    out_tokens: int = 0
    estimated: bool = True


def parse_facts(raw: str) -> dict[str, str] | None:
    """Парсит JSON-объект фактов из ответа модели; None — распарсить не удалось.

    Терпим к markdown-обёртке ```json …``` и тексту вокруг JSON-объекта.
    Пустые ключи и значения отбрасываются.
    """
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline == -1:
            return None
        text = text[first_newline + 1 :]
        end_fence = text.rfind("```")
        if end_fence != -1:
            text = text[:end_fence]
        text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    result: dict[str, str] = {}
    for raw_key, raw_value in data.items():
        key, value = str(raw_key).strip(), str(raw_value).strip()
        if key and value:
            result[key] = value
    return result


class FactsExtractor:
    """Обновляет facts одним LLM-вызовом (plain Python, без UI)."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def update(
        self,
        *,
        model: str,
        api_base: str,
        api_key: str,
        facts: dict[str, str],
        history: list[Message],
    ) -> FactsResult:
        """Возвращает обновлённый набор фактов (может совпасть со старым).

        `history` — хвост диалога, включая только что добавленное сообщение
        пользователя. Расход вызова: точный (usage в стриме) либо оценка
        chars/4 — в результате.
        """
        context = history[-FACTS_CONTEXT_MESSAGES:] if history else []
        body = (
            f"Текущие факты:\n{json.dumps(facts, ensure_ascii=False)}\n\n"
            "Последние сообщения диалога:\n"
            + self._transcript(context)
            + "\n\nВерни обновлённый JSON-объект фактов."
        )
        request = ChatRequest(
            model=model,
            messages=[
                Message(role=Role.SYSTEM, content=FACTS_EXTRACTOR_PROMPT),
                Message(role=Role.USER, content=body),
            ],
            temperature=0.0,
            max_tokens=FACTS_MAX_TOKENS,
        )
        text = ""
        reasoning_text = ""
        server_usage: Usage | None = None
        async for chunk in self._llm.astream(request, api_base, api_key):
            if chunk.usage is not None:
                server_usage = chunk.usage
            if chunk.content:
                text += chunk.content
            if chunk.reasoning:
                reasoning_text += chunk.reasoning
        parsed = parse_facts(text)
        if parsed is None:
            raise LLMError(
                "извлекатель facts вернул не-JSON ответ "
                f"(finish_reason: {chunk.finish_reason or '—'})"
            )
        if server_usage is not None:
            return FactsResult(
                facts=parsed,
                in_tokens=server_usage.prompt_tokens,
                out_tokens=server_usage.completion_tokens,
                estimated=False,
            )
        return FactsResult(
            facts=parsed,
            in_tokens=estimate_messages(request.messages),
            out_tokens=estimate_text(text) + estimate_text(reasoning_text),
        )

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
