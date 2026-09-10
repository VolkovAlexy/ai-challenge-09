"""StatusBar: имя агента, provider:model, temperature/top_p/max_tokens, context, «не сохранено»."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget

if TYPE_CHECKING:
    from my_agent.core.agent import Agent
    from my_agent.ui.app import ChatTab


def fmt_tokens(n: int) -> str:
    """Компактный формат: 456 / 2039 / 12.3k / 1.5M (до 10k — точное число)."""
    if n < 10_000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


def context_part(agent: Agent) -> Text | None:
    """«context ~12.3k» — вес контекста прямо сейчас: system + вся история.

    ~ — оценка (нет точных чисел от API). При отправке нового сообщения
    реальный промпт будет больше ровно на токены этого сообщения.
    ⚠ — последний ход обрезан провайдером (usage заметно меньше отправленного).
    None — первого ответа ещё нет: контекст неизвестен, индикатор скрыт.
    """
    if not agent.has_first_response:
        return None
    tokens, estimated = agent.context_now
    mark = "~" if estimated else ""
    truncated = agent.last_usage is not None and agent.last_usage.truncated
    if truncated:
        return Text(f"⚠ context {mark}{fmt_tokens(tokens)}", style="yellow")
    return Text(f"context {mark}{fmt_tokens(tokens)}", style="dim")


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
            Text(f"temp {s.temperature:g}"),
            Text(f"top_p {s.top_p:g}"),
            Text(f"max {s.max_tokens}"),
        ]
        context = context_part(agent)
        if context is not None:
            parts.append(context)
        if s.stop:
            parts.append(Text("stop: " + ", ".join(s.stop)))
        text = Text()
        for part in parts:
            text.append_text(part)
            text.append("   ")
        return text
