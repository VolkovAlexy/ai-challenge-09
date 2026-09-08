"""SessionPalette: модальная палитра всех сохранённых сессий (команда /session).

Список из SessionStore (newest-first, без пустых сессий) + фильтр по теме.
Строка: дата-время последней активности + тема (первое сообщение пользователя).
Текущая сессия активного агента помечена `*`. Enter применяет выбранную
(dismiss с её id), Esc — отмена (dismiss None).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from my_agent.memory.persistence import SessionInfo


def _format_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class SessionPalette(ModalScreen[str]):
    """Палитра сессий: фильтр, ↑↓ — навигация, Enter — загрузить в активного агента."""

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "dismiss", "Закрыть"),
        Binding("up", "move_highlight(-1)", "Up"),
        Binding("down", "move_highlight(+1)", "Down"),
    ]

    DEFAULT_CSS = """
    SessionPalette {
        align: center middle;
        layout: vertical;
    }
    SessionPalette Input {
        width: 80;
    }
    SessionPalette OptionList {
        width: 80;
        height: auto;
        max-height: 15;
        border: solid cyan;
    }
    SessionPalette Static {
        width: 80;
        color: $text-disabled;
    }
    """

    def __init__(self, sessions: list[SessionInfo], current_id: str | None = None) -> None:
        super().__init__()
        self._sessions = sessions
        self._current = current_id
        self._filter = Input(placeholder="фильтр по теме…")
        self._list = OptionList()

    def compose(self) -> Iterator[Widget]:
        yield self._filter
        yield self._list
        yield Static("↑↓ — выбор · Enter — загрузить · Esc — отмена")

    async def on_mount(self) -> None:
        self._rebuild("")
        self._filter.focus()

    def _label(self, info: SessionInfo) -> str:
        mark = "* " if info.id == self._current else "  "
        return f"{mark}{_format_time(info.updated_at)}  {info.title}"

    def _rebuild(self, text: str) -> None:
        """Перестроить список по фильтру; выделить текущую сессию, если видна."""
        needle = text.casefold()
        matches = [info for info in self._sessions if needle in info.title.casefold()]
        self._list.clear_options()
        if matches:
            self._list.add_options(Option(self._label(info), id=info.id) for info in matches)
            highlight = 0
            for i, info in enumerate(matches):
                if info.id == self._current:
                    highlight = i
                    break
            self._list.highlighted = highlight
        else:
            self._list.add_options([Option("нет сессий", disabled=True)])
            self._list.highlighted = None

    def _highlighted_id(self) -> str | None:
        option = self._list.highlighted_option
        if option is None or option.disabled or option.id is None:
            return None
        return option.id

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        session_id = self._highlighted_id()
        if session_id is not None:
            self.dismiss(session_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(event.option_id)

    def action_move_highlight(self, direction: int) -> None:
        count = self._list.option_count
        if count == 0:
            return
        current = self._list.highlighted
        if current is None:
            self._list.highlighted = 0
        else:
            self._list.highlighted = max(0, min(count - 1, current + direction))
