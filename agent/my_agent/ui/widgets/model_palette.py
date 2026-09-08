"""ModelPalette: модальная палитра выбора модели (команда /model без аргумента).

Фильтр + список моделей из config; текущая помечена `*`. Enter применяет
выбранную модель (dismiss с id 'provider:model'), Esc — отмена (dismiss None).
"""

from __future__ import annotations

from collections.abc import Iterator

from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class ModelPalette(ModalScreen[str]):
    """Палитра выбора модели: фильтр, ↑↓ — навигация, Enter — применить."""

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "dismiss", "Закрыть"),
        Binding("up", "move_highlight(-1)", "Up"),
        Binding("down", "move_highlight(+1)", "Down"),
    ]

    DEFAULT_CSS = """
    ModelPalette {
        align: center middle;
        layout: vertical;
    }
    ModelPalette Input {
        width: 60;
    }
    ModelPalette OptionList {
        width: 60;
        height: auto;
        max-height: 15;
        border: solid cyan;
    }
    ModelPalette Static {
        width: 60;
        color: $text-disabled;
    }
    """

    def __init__(self, model_ids: list[str], current: str | None = None) -> None:
        super().__init__()
        self._model_ids = model_ids
        self._current = current
        self._filter = Input(placeholder="фильтр…")
        self._list = OptionList()

    def compose(self) -> Iterator[Widget]:
        yield self._filter
        yield self._list
        yield Static("↑↓ — выбор · Enter — применить · Esc — отмена")

    async def on_mount(self) -> None:
        self._rebuild("")
        self._filter.focus()

    def _rebuild(self, text: str) -> None:
        """Перестроить список по фильтру; выделить текущую модель, если видна."""
        needle = text.casefold()
        matches = [m for m in self._model_ids if needle in m.casefold()]
        self._list.clear_options()
        if matches:
            self._list.add_options(
                Option(f"* {m}" if m == self._current else m, id=m) for m in matches
            )
            self._list.highlighted = (
                matches.index(self._current) if self._current in matches else 0
            )
        else:
            self._list.add_options([Option("нет совпадений", disabled=True)])
            self._list.highlighted = None

    def _highlighted_id(self) -> str | None:
        option = self._list.highlighted_option
        if option is None or option.disabled or option.id is None:
            return None
        return option.id

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        model_id = self._highlighted_id()
        if model_id is not None:
            self.dismiss(model_id)

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
