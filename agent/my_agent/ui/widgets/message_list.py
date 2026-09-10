"""MessageList: история диалога + текущий стриминг + заметки команд.

Под репликами ассистента — строка «tokens: in N · out M» (usage хода,
runtime-данные агента). Пока стрим не начал выводить контент — лоадер
«думаю…» (спиннер).

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
from rich.spinner import Spinner
from rich.text import Text
from textual.events import Resize
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.visual import Visual, visualize

from my_agent.core.context import fmt_tokens
from my_agent.core.message import Role

if TYPE_CHECKING:
    from my_agent.ui.app import ChatTab, Note

_ROLE_TITLES = {"user": "вы", "assistant": "ассистент", "system": "система", "tool": "tool"}
_ROLE_COLORS = {"user": "cyan", "assistant": "green"}

# Заметки (вывод команд, ошибки) — те же баблы, что и реплики: kind → (заголовок, обводка)
_NOTE_STYLES = {
    "error": ("ошибка", "red"),
    "system": ("инфо", "orange1"),
    "warning": ("внимание", "yellow"),
    "compact": ("сжатие", "cyan"),
}


def _tokens_line(in_tokens: int, out_tokens: int, estimated: bool) -> Text:
    """Dim-строка «tokens: in 120 · out 50» под репликой ассистента (~ при оценке).

    in — полный промпт хода (system + история: каждый запрос переотправляет
    контекст целиком), out — ответ модели. Во время стрима in ещё неизвестен
    (in_tokens == 0) — печатается только out.
    """
    mark = "~" if estimated else ""
    if in_tokens > 0:
        return Text(
            f"  tokens: in {mark}{fmt_tokens(in_tokens)} · out {mark}{fmt_tokens(out_tokens)}",
            style="dim",
        )
    return Text(f"  tokens: out {mark}{fmt_tokens(out_tokens)}", style="dim")


def _note_panel(note: Note) -> Panel:
    """Бабл заметки: тот же Panel, что у реплик, с обводкой по kind."""
    title, border = _NOTE_STYLES.get(note.kind, ("инфо", "orange1"))
    return Panel(Text(note.text), title=title, border_style=border)


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

        # Заметки вшиваем в таймлайн чата: pos = «перед сообщением №pos».
        # Clamp — история могла усохнуть (compact / загрузка сессии).
        notes_at: dict[int, list[Note]] = {}
        for note in tab.notes:
            pos = min(note.anchor, len(history))
            notes_at.setdefault(pos, []).append(note)

        blocks: list[RenderableType] = []
        for index, message in enumerate(history):
            for note in notes_at.get(index, []):
                blocks.append(_note_panel(note))
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
            if message.role == Role.ASSISTANT and (u := agent.message_usage.get(index)) is not None:
                blocks.append(_tokens_line(u.prompt_tokens, u.completion_tokens, u.estimated))

        if agent.is_streaming:
            title = _ROLE_TITLES["assistant"]
            color = _ROLE_COLORS["assistant"]
            if agent.is_thinking:
                # до первого контента показываем лоадер вместо пустой панели
                # (во время LLM-вызова суммаризации — «сжимаю контекст…»)
                loading = "сжимаю контекст…" if agent.is_compacting else "думаю…"
                blocks.append(
                    Panel(
                        Spinner("dots", text=Text(loading, style="dim")),
                        title=f"{title} …",
                        border_style=color,
                    )
                )
            else:
                stream = agent.streaming_text or " "
                tcs = agent.streaming_tool_calls
                names = ", ".join(t.function.name or "?" for t in tcs)
                extra = f"  [tool_calls: {names}]" if tcs else ""
                blocks.append(
                    Panel(Text(stream + "▌" + extra), title=f"{title} …", border_style=color)
                )
                blocks.append(_tokens_line(0, agent.streaming_out_estimate, estimated=True))

        # хвост: заметки, случившиеся после последнего сообщения (и при пустой истории)
        for note in notes_at.get(len(history), []):
            blocks.append(_note_panel(note))

        return Group(*blocks)
