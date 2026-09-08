"""MessageList: история диалога + текущий стриминг + заметки команд.

Рендерится из состояния ChatTab/Agent (plain Python); перерисовка
управляется таймером App (~100 мс), а не каждым токеном.

Скролл: line-API ScrollView (как RichLog) — `render()` возвращает
rich-рендеруемое, а полную высоту (strips + virtual_size) и смещение
scroll_offset виджет считает сам.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text
from textual.events import Resize
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.visual import Visual, visualize

if TYPE_CHECKING:
    from my_agent.ui.app import ChatTab

_ROLE_TITLES = {"user": "вы", "assistant": "ассистент", "system": "система", "tool": "tool"}
_ROLE_COLORS = {"user": "cyan", "assistant": "green"}


class MessageList(ScrollView):
    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    """

    def __init__(self, tab: ChatTab) -> None:
        super().__init__()
        self.tab = tab
        self._strips: list[Strip] = []
        # прилипать к низу, пока пользователь сам не проскроллит вверх
        self._follow = True

    @property
    def at_bottom(self) -> bool:
        """Проскроллен ли вид к последнему сообщению."""
        return self.max_scroll_y - self.scroll_y < 1.0

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Запоминать, прижат ли пользователь к низу (для авто-скролла)."""
        super().watch_scroll_y(old_value, new_value)
        self._follow = new_value >= self.max_scroll_y - 1.0

    def refresh_follow(self) -> None:
        """Перерисовать список; доскроллить к низу, если пользователь у низа.

        Если пользователь проскроллил вверх (читает историю), его не
        отрывать к последнему сообщению — докатым при следующем
        приближении к низу.
        """
        self._rebuild_strips()
        self.refresh()
        if self._follow:
            self.scroll_end(animate=False)

    def on_resize(self, event: Resize) -> None:
        """Содержимое переносится по ширине — пересчитать strips при смене размера."""
        self._rebuild_strips()

    def _rebuild_strips(self) -> None:
        """Отрендерить список в strips полной высоты и задать virtual_size."""
        width = self.size.width
        if width <= 0:
            self._strips = []
            self.virtual_size = Size(0, 0)
            return
        visual = visualize(self, self.render(), markup=self._render_markup)
        self._strips = Visual.to_strips(self, visual, width, None, self.visual_style)
        self.virtual_size = Size(width, len(self._strips))

    def render_line(self, y: int) -> Strip:
        """Строка в координатах viewport → строка strips полной высоты."""
        _, scroll_y = self.scroll_offset
        index = int(scroll_y) + y
        if 0 <= index < len(self._strips):
            return self._strips[index]
        return Strip.blank(self.size.width, self.visual_style.rich_style)

    def render(self) -> RenderableType:
        tab = self.tab
        agent = tab.agent
        history = agent.memory.history
        if not history and not agent.is_streaming and not tab.notes:
            return Text("Начните диалог. /help — список команд.", style="dim")

        blocks: list[RenderableType] = []
        for message in history:
            title = _ROLE_TITLES.get(message.role.value, message.role.value)
            color = _ROLE_COLORS.get(message.role.value, "dim")
            if message.content:
                body: Text = Text(message.content)
            elif message.tool_calls:
                names = ", ".join(tc.function.name or tc.id or "?" for tc in message.tool_calls)
                body = Text(f"[tool_calls] {names}", style="dim italic")
            else:
                body = Text("(пусто)", style="dim")
            blocks.append(Panel(body, title=title, border_style=color))

        if agent.is_streaming:
            title = _ROLE_TITLES["assistant"]
            color = _ROLE_COLORS["assistant"]
            stream = agent.streaming_text or " "
            tcs = agent.streaming_tool_calls
            names = ", ".join(t.function.name or "?" for t in tcs)
            extra = f"  [tool_calls: {names}]" if tcs else ""
            blocks.append(Panel(Text(stream + "▌" + extra), title=f"{title} …", border_style=color))

        for kind, text in tab.notes:
            style = "red" if kind == "error" else "dim"
            blocks.append(Text(text, style=style))

        return Group(*blocks)
