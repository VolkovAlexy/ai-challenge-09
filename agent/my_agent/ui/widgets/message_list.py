"""MessageList: история диалога + текущий стриминг + заметки команд.

Под репликами ассистента — строка «tokens: in N · out M · think K» (usage хода,
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

from rich.box import Box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text
from textual.events import Resize
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.visual import Visual, visualize

from my_agent.core.context import estimate_text, fmt_tokens
from my_agent.core.message import Message, Role
from my_agent.ui.colors import ANSWER_STYLE, REASONING_STYLE, TOKENS_STYLE, USER_BUBBLE_BG

if TYPE_CHECKING:
    from my_agent.ui.app import ChatTab, Note

# пустой box (в Rich 15 константа box.EMPTY удалена): ни одного символа рамки —
# бабл пользователя рисуется фоном без обводки
BUBBLE_BOX = Box("    \n    \n    \n    \n    \n    \n    \n    ")

# box только с левой вертикальной линией — размышления выглядят цитатой
REASONING_BOX = Box("│   \n│   \n│   \n│   \n│   \n│   \n│   \n│   ")

# Заметки (вывод команд, ошибки) — цитаты с цветной полосой слева по kind
# (реплики — без рамок): [заголовок, цвет, иконка типа].
_NOTE_STYLES = {
    "error": ("ошибка", "red", "✗"),
    "system": ("инфо", "orange1", "ℹ"),
    "warning": ("внимание", "yellow", "⚠"),
    "compact": ("сжатие", "cyan", "⇄"),
    "facts": ("facts", "magenta", "🗂"),
}


def _tokens_line(
    in_tokens: int, out_tokens: int, think_tokens: int, estimated: bool
) -> Text:
    """Dim-строка «tokens: in 120 · out 50 · think 2k» под репликой ассистента (~ при оценке).

    in — полный промпт хода (system + история: каждый запрос переотправляет
    контекст целиком), out — видимый ответ модели, think — токены размышлений
    thinking-моделей (в out статус-бара входят и они, здесь выделены отдельно).
    Во время стрима in ещё неизвестен (in_tokens == 0) — печатается без in.
    Строка слева с иконкой ◈ и отделена пустой строкой (см. _tokens_block).
    """
    mark = "~" if estimated else ""
    out = f"out {mark}{fmt_tokens(out_tokens)}"
    if think_tokens > 0:
        out += f" · think {mark}{fmt_tokens(think_tokens)}"
    if in_tokens > 0:
        body = f"tokens: in {mark}{fmt_tokens(in_tokens)} · {out}"
    else:
        body = f"tokens: {out}"
    return Text(f"  ◈ {body}", style=TOKENS_STYLE)


def _tokens_block(
    in_tokens: int, out_tokens: int, think_tokens: int, estimated: bool
) -> list[RenderableType]:
    """[пустая строка-разделитель, tokens-строка] — чтобы отделить от реплики."""
    return [Text(""), _tokens_line(in_tokens, out_tokens, think_tokens, estimated)]


def _note_panel(note: Note) -> Panel:
    """Бабл заметки: тонкая полоса слева цвета типа + иконка и заголовок (реплики — без рамок)."""
    title, border, icon = _NOTE_STYLES.get(note.kind, ("инфо", "orange1", "ℹ"))
    return Panel(
        Text(note.text),
        box=REASONING_BOX,
        border_style=border,
        title=f"{icon} {title}",
        title_align="left",
    )


def _reasoning_block(text: str) -> Panel:
    """Блок размышлений: приглушённо-серый текст с серой полосой слева (цитата)."""
    return Panel(
        Text(text, style=REASONING_STYLE),
        box=REASONING_BOX,
        border_style=REASONING_STYLE,
    )


def _message_blocks(message: Message) -> list[RenderableType]:
    """Реплика без обводки: пользователь — тёмно-серый бабл, ассистент — текст.

    Роли не подписываются — различаются фоном бабла. После бабла пользователя
    идёт пустая строка-отступ. У ассистента сначала идёт полный текст
    размышлений (thinking-модели) цитатой с серой полосой, затем пустая
    строка и белый ответ.
    """
    if message.role == Role.USER:
        body = Text(message.content) if message.content else Text("(пусто)", style="dim")
        return [Panel(body, box=BUBBLE_BOX, style=f"on {USER_BUBBLE_BG}"), Text("")]
    blocks: list[RenderableType] = []
    if message.reasoning:
        blocks.append(_reasoning_block(message.reasoning))
        blocks.append(Text(""))
    if message.content:
        blocks.append(Text(message.content, style=ANSWER_STYLE))
    elif message.tool_calls:
        names = ", ".join(tc.function.name or tc.id or "?" for tc in message.tool_calls)
        blocks.append(Text(f"[tool_calls] {names}", style="dim italic"))
    elif not message.reasoning:
        blocks.append(Text("(пусто)", style="dim"))
    return blocks


class MessageList(ScrollView):
    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        overflow-y: auto;
        overflow-x: hidden;
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
            blocks.extend(_message_blocks(message))
            if message.role == Role.ASSISTANT and (u := agent.message_usage.get(index)) is not None:
                blocks.extend(
                    _tokens_block(
                        u.prompt_tokens, u.answer_tokens, u.reasoning_tokens, u.estimated
                    )
                )

        # хвостовые заметки — до стрим-блока: заметка о сжатии вставляется
        # после запроса пользователя, и при старте стрима она должна остаться
        # между запросом и ответом, а не уезжать вниз вместе с растущим стримом
        for note in notes_at.get(len(history), []):
            blocks.append(_note_panel(note))

        if agent.is_streaming:
            if agent.is_compacting:
                # (во время LLM-вызова суммаризации — «сжимаю контекст…»)
                blocks.append(Spinner("dots", text=Text("сжимаю контекст…", style="dim")))
            elif agent.is_extracting_facts:
                # (во время LLM-вызова обновления facts — «обновляю facts…»)
                blocks.append(Spinner("dots", text=Text("обновляю facts…", style="dim")))
            elif agent.streaming_reasoning and not agent.streaming_text:
                # thinking-модель стримит размышления — показываем их целиком, цитатой
                blocks.append(_reasoning_block(agent.streaming_reasoning + "▌"))
            elif agent.is_thinking:
                # до первого контента показываем лоадер
                blocks.append(Spinner("dots", text=Text("думаю…", style="dim")))
            else:
                stream = agent.streaming_text or " "
                tcs = agent.streaming_tool_calls
                names = ", ".join(t.function.name or "?" for t in tcs)
                extra = f"  [tool_calls: {names}]" if tcs else ""
                blocks.append(Text(stream + "▌" + extra, style=ANSWER_STYLE))
                # live-оценки: out по видимому тексту, think — по размышлениям
                blocks.extend(
                    _tokens_block(
                        0,
                        agent.streaming_out_estimate,
                        estimate_text(agent.streaming_reasoning),
                    estimated=True,
                )
            )

        return Group(*blocks)
