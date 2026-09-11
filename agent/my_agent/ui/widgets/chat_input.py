"""ChatInput: многострочное поле ввода + tab-completion по командам и моделям.

Enter — отправить, Shift+Enter — перенос строки, Tab — автодополнение
(readline-стиль: один кандидат — подставляется, несколько — общий префикс,
повторный Tab перебирает кандидатов по кругу). Вставка многострочного текста
работает нативно. Высота растёт вместе с содержимым до MAX_INPUT_LINES.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from my_agent.ui.colors import USER_BUBBLE_BG_CSS

if TYPE_CHECKING:
    from my_agent.ui.app import AgentApp, ChatTab

MAX_INPUT_LINES = 8  # максимум видимых строк ввода (+2 на вертикальный padding)


class ChatInput(TextArea):
    DEFAULT_CSS = f"""
    ChatInput {{
        border: none;
        background: {USER_BUBBLE_BG_CSS};
        color: white;
        padding: 1 2;
    }}
    ChatInput:focus {{
        border: none;
    }}
    """

    BINDINGS = [  # noqa: RUF012
        # priority=True — перехват до обработки клавиш самим TextArea.
        Binding("enter", "submit", "Отправить", priority=True),
        Binding("shift+enter", "newline", "Новая строка", priority=True),
        Binding("tab", "complete", "Tab", priority=True),
    ]

    @dataclass
    class Submitted(Message):
        """Текст отправлен (Enter)."""

        input: ChatInput
        value: str

        @property
        def control(self) -> ChatInput:
            """Псевдоним input (идиома Textual)."""
            return self.input

    def __init__(self, tab: ChatTab) -> None:
        super().__init__(
            placeholder="Сообщение или /команда…",
            highlight_cursor_line=False,
        )
        self.tab = tab
        self._candidates: list[str] = []
        self._index = 0
        self.styles.height = 3  # одна строка + отступ сверху и снизу

    # --- совместимость с прежним API (Input.value) ---

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text  # load_text ставит курсор в начало — возвращаем в конец
        self.move_cursor(self.document.end)

    # --- авто-высота по числу (завёрнутых) строк ---

    def _update_height(self) -> None:
        lines = min(self.wrapped_document.height, MAX_INPUT_LINES)
        self.styles.height = max(lines, 1) + 2  # + строка отступа сверху и снизу

    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._update_height()

    def _on_resize(self) -> None:
        super()._on_resize()  # rewrap по новой ширине
        self._update_height()

    # --- действия ---

    def action_submit(self) -> None:
        self.post_message(self.Submitted(self, self.text))

    def action_newline(self) -> None:
        self.insert("\n")

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
