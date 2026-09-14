"""StatusBar: имя агента, provider:model, стратегия контекста, context, «не сохранено»."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget

from my_agent.core.context import fmt_tokens

if TYPE_CHECKING:
    from my_agent.core.agent import Agent
    from my_agent.ui.app import ChatTab


def context_part(agent: Agent) -> Text | None:
    """«context 12.3k/32k (38%)» — вес проекции следующего запроса к LLM.

    Проекция: system + саммари + несжатый хвост (набираемый текст не входит —
    он добавится в момент отправки). ~ — оценка (нет точных чисел от API).
    Жёлтый — заполнение окна выше compaction_threshold: при следующем сообщении
    старейший несжатый префикс будет сжат в саммари (чат не меняется).
    ⚠ — последний ход обрезан провайдером (usage заметно меньше отправленного).
    None — первого ответа ещё нет: контекст неизвестен, индикатор скрыт.
    """
    if not agent.has_first_response:
        return None
    tokens, estimated = agent.context_now
    mark = "~" if estimated else ""
    window = agent.context_window
    share = tokens / window if window > 0 else 0.0
    tail = f"/{fmt_tokens(window)} ({share:.0%})"
    truncated = agent.last_usage is not None and agent.last_usage.truncated
    if truncated:
        return Text(f"⚠ context {mark}{fmt_tokens(tokens)}{tail}", style="yellow")
    if agent.context_share is not None and agent.context_share >= agent.compaction_threshold:
        return Text(f"context {mark}{fmt_tokens(tokens)}{tail}", style="yellow")
    return Text(f"context {mark}{fmt_tokens(tokens)}{tail}", style="dim")


def totals_part(agent: Agent) -> Text:
    """«in 12.3k out 1.2k Σ 13.5k» — накопительный расход токенов за сессию.

    in — Σ prompt_tokens всех запросов (каждый запрос переотправляет контекст
    целиком — это честный расход), out — Σ completion_tokens ответов,
    Σ — всего потрачено; включают и LLM-вызовы сжатия контекста.
    ~ — в числах есть локальные оценки (API не вернул usage за какой-то ход).
    """
    totals = agent.totals
    mark = "~" if totals.estimated else ""
    return Text(
        f"in {mark}{fmt_tokens(totals.in_tokens)}"
        f"  out {mark}{fmt_tokens(totals.out_tokens)}"
        f"  Σ {mark}{fmt_tokens(totals.total_tokens)}",
        style="dim",
    )


def strategy_part(agent: Agent) -> Text:
    """«ctx strategy:<режим>» — активная стратегия контекста, видна всегда.

    summary — dim (дефолт), none — жёлтый (напоминание, что история уходит
    в LLM целиком), sliding — cyan, facts — magenta; в sliding/facts
    после имени показывается размер окна: «ctx strategy:sliding·w20».
    """
    strategy = agent.settings.context_strategy
    if strategy == "none":
        return Text("ctx strategy:none", style="yellow")
    if strategy == "facts":
        return Text(f"ctx strategy:facts·w{agent.settings.sliding_window}", style="magenta")
    if strategy == "sliding":
        return Text(f"ctx strategy:sliding·w{agent.settings.sliding_window}", style="cyan")
    return Text("ctx strategy:summary", style="dim")


def branch_part(agent: Agent) -> Text | None:
    """«⎇ ветка» — только когда веток больше одной (иначе это шум)."""
    names = agent.memory.branch_names()
    if len(names) <= 1:
        return None
    return Text(f"⎇ {agent.memory.active_branch}", style="green")


class StatusBar(Widget):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    """

    def __init__(self, tab: ChatTab) -> None:
        super().__init__()
        self.tab = tab

    def render(self) -> Text:
        agent = self.tab.agent
        s = agent.settings
        parts = [
            Text(agent.name, style="bold"),
            Text(s.model + (" •" if agent.settings_dirty else ""), style="bold yellow"),
            strategy_part(agent),
        ]
        context = context_part(agent)
        if context is not None:
            parts.append(context)
        parts.append(totals_part(agent))
        branch = branch_part(agent)
        if branch is not None:
            parts.append(branch)
        if s.stop:
            parts.append(Text("stop: " + ", ".join(s.stop)))
        text = Text()
        for part in parts:
            text.append_text(part)
            text.append("   ")
        return text
