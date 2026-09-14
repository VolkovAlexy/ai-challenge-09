"""StrategyPalette: модальная палитра выбора стратегии контекста (/strategy без аргумента).

Список из четырёх стратегий с описаниями; текущая помечена `*`.
Enter применяет выбор (dismiss со строкой-литералом), Esc — отмена (dismiss None).
"""

from __future__ import annotations

from collections.abc import Iterator

from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from my_agent.commands.registry import STRATEGIES, STRATEGY_DESCRIPTIONS

DESCRIPTION_WIDTH = 74


class StrategyPalette(ModalScreen[str]):
    """Палитра стратегии контекста: ↑↓ — навигация, Enter — применить."""

    BINDINGS = [  # noqa: RUF012
        Binding("escape", "dismiss", "Закрыть"),
        Binding("up", "move_highlight(-1)", "Up"),
        Binding("down", "move_highlight(+1)", "Down"),
    ]

    DEFAULT_CSS = f"""
    StrategyPalette {{
        align: center middle;
        layout: vertical;
    }}
    StrategyPalette OptionList {{
        width: {DESCRIPTION_WIDTH};
        height: auto;
        max-height: 8;
        border: solid magenta;
    }}
    StrategyPalette Static {{
        width: {DESCRIPTION_WIDTH};
        color: $text-disabled;
    }}
    """

    def __init__(self, current: str | None = None) -> None:
        super().__init__()
        self._current = current
        self._list = OptionList()

    def compose(self) -> Iterator[Widget]:
        yield self._list
        yield Static("↑↓ — выбор · Enter — применить · Esc — отмена")

    async def on_mount(self) -> None:
        self._rebuild()
        self._list.focus()

    def _rebuild(self) -> None:
        """Список стратегий с описаниями; выделить текущую."""
        self._list.clear_options()
        self._list.add_options(
            Option(
                f"* {s} — {STRATEGY_DESCRIPTIONS[s]}"
                if s == self._current
                else f"  {s} — {STRATEGY_DESCRIPTIONS[s]}",
                id=s,
            )
            for s in STRATEGIES
        )
        if self._current in STRATEGIES:
            self._list.highlighted = STRATEGIES.index(self._current)

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
