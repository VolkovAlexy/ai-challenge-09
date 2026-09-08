"""ChatInput: строка ввода + tab-completion по командам и моделям.

Readline-стиль: один кандидат — подставляется, несколько — общий префикс,
повторный Tab перебирает кандидатов по кругу.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

from textual.binding import Binding
from textual.widgets import Input

if TYPE_CHECKING:
    from my_agent.ui.app import AgentApp, ChatTab


class ChatInput(Input):
    BINDINGS = [Binding("tab", "complete", "Tab")]  # noqa: RUF012

    def __init__(self, tab: ChatTab) -> None:
        super().__init__(placeholder="Сообщение или /команда…")
        self.tab = tab
        self._candidates: list[str] = []
        self._index = 0

    def _app(self) -> AgentApp | None:
        app = self.app
        if hasattr(app, "complete_command"):
            return cast("AgentApp", app)
        return None

    def action_complete(self) -> None:
        app = self._app()
        if app is None or not self.value.startswith("/"):
            return
        candidates = app.complete_command(self.value)
        if not candidates:
            self._candidates = []
            return
        # Команду уже ввели (есть пробел) — дописываем только аргумент,
        # префикс команды сохраняем.
        base = self.value[: self.value.rfind(" ") + 1] if " " in self.value else ""
        if len(candidates) == 1:
            self._candidates = []
            self.value = base + candidates[0]
            if not base:
                self.value += " "
            return
        if candidates != self._candidates:
            self._candidates = candidates
            self._index = 0
        prefix = os.path.commonprefix(candidates)
        if base + prefix != self.value:
            self.value = base + prefix
        else:
            # Префикс уже максимальный — показываем кандидатов по кругу
            # (первый Tab — первый кандидат, далее — по порядку).
            self.value = base + self._candidates[self._index]
            self._index = (self._index + 1) % len(self._candidates)
