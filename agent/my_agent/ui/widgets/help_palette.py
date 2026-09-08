"""HelpPalette: модальный список команд (/help, F1, tab-completion подсказка)."""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static


class HelpPalette(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Закрыть")]  # noqa: RUF012

    DEFAULT_CSS = """
    HelpPalette {
        align: center middle;
    }
    HelpPalette > Widget {
        width: auto;
        max-width: 90%;
    }
    """

    def __init__(self, title: str, entries: list[tuple[str, str, str]]) -> None:
        super().__init__()
        self._title = title
        self._entries = entries

    def compose(self) -> Iterator[Widget]:
        yield Static(self._build_panel())

    def _build_panel(self) -> Panel:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column(style="dim", no_wrap=True)
        table.add_column()
        for name, spec, description in self._entries:
            table.add_row(f"/{name}", spec, description)
        if not self._entries:
            table.add_row(Text("нет вариантов", style="dim"))
        return Panel(Group(table), title=self._title, border_style="cyan")
