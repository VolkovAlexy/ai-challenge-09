"""StatusBar: имя агента, provider:model, temperature/top_p/max_tokens, stop, «не сохранено»."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widget import Widget

if TYPE_CHECKING:
    from my_agent.ui.app import ChatTab


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
        if s.stop:
            parts.append(Text("stop: " + ", ".join(s.stop)))
        text = Text()
        for part in parts:
            text.append_text(part)
            text.append("   ")
        return text
