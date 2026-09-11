"""MessageList: скролл длинного чата и авто-скролл к последнему сообщению."""

import asyncio
import contextlib

from rich.panel import Panel
from textual.pilot import Pilot

from my_agent.config.schema import validate_config
from my_agent.core.message import Message
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp, Note
from my_agent.ui.colors import REASONING_STYLE
from my_agent.ui.widgets.message_list import MessageList, _note_panel


def make_app() -> AgentApp:
    config = validate_config(
        {
            "providers": {
                "alpha": {"api_base": "http://a/v1", "api_key": "", "models": ["m1"]},
            },
            "default_model": "alpha:m1",
        }
    )
    return AgentApp(
        config=config,
        llm=LLMClient(),
        tools=ToolRegistry(),
        default_system_prompt="SP",
    )


def fill_history(app: AgentApp, count: int, prefix: str = "msg") -> None:
    agent = app.active_agent()
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        agent.memory.add(Message(role=role, content=f"{prefix}-{i} " + "lorem " * 8))


async def redraw(app: AgentApp, pilot: Pilot) -> MessageList:
    """Прогнать батч-перерисовку активной вкладки (как делает таймер App)."""
    tab = app.active_tab()
    tab.dirty = True
    app._view_of(tab).refresh_all()
    await pilot.pause()
    return app.query_one(MessageList)


async def settle(pilot: Pilot, value) -> None:
    """Ждать, пока значение перестанет меняться (отложенный scroll_end добежит)."""
    last = value()
    for _ in range(20):
        await pilot.pause()
        if value() == last:
            return
        last = value()
    raise AssertionError("scroll position did not settle")


async def test_long_chat_is_scrollable() -> None:
    app = make_app()
    async with app.run_test(size=(60, 15)) as pilot:
        fill_history(app, 40)
        messages = await redraw(app, pilot)
        await settle(pilot, lambda: messages.max_scroll_y)
        assert messages.max_scroll_y > 0


async def test_new_messages_follow_to_bottom() -> None:
    app = make_app()
    async with app.run_test(size=(60, 15)) as pilot:
        fill_history(app, 10)
        messages = await redraw(app, pilot)
        fill_history(app, 30, prefix="more")
        messages = await redraw(app, pilot)
        await settle(pilot, lambda: messages.scroll_y)
        assert messages.max_scroll_y > 0
        assert messages.at_bottom


async def test_reading_history_is_not_yanked() -> None:
    app = make_app()
    async with app.run_test(size=(60, 15)) as pilot:
        fill_history(app, 40)
        messages = await redraw(app, pilot)
        await settle(pilot, lambda: messages.scroll_y)
        messages.scroll_home(animate=False, immediate=True)
        await pilot.pause()
        assert messages.scroll_y == 0.0
        fill_history(app, 20, prefix="more")
        messages = await redraw(app, pilot)
        await settle(pilot, lambda: messages.scroll_y)
        assert messages.scroll_y == 0.0


async def test_compaction_note_stays_above_streaming_reasoning() -> None:
    """Заметка о сжатии (anchor = конец истории) не уезжает вниз при стриме.

    Заметка вставляется между запросом пользователя и будущим ответом;
    когда начинается стрим размышлений, она должна остаться на своём
    месте — до стрим-блока, а не после него (не прилипать к низу чата).
    """
    app = make_app()
    async with app.run_test(size=(60, 15)) as pilot:
        fill_history(app, 2)
        tab = app.active_tab()
        agent = tab.agent
        tab.add_note("compact", "Контекст сжат: -1k удалено, +100 саммари")
        agent._task = asyncio.create_task(asyncio.sleep(60))  # симуляция стрима
        try:
            agent._stream_reasoning = "размышляю…"
            messages = await redraw(app, pilot)
            blocks = list(messages.render().renderables)
            note_idx = next(
                i
                for i, b in enumerate(blocks)
                if isinstance(b, Panel) and b.title is not None and "сжатие" in str(b.title)
            )
            reason_idx = next(
                i
                for i, b in enumerate(blocks)
                if isinstance(b, Panel) and b.title is None and b.border_style == REASONING_STYLE
            )
            assert note_idx < reason_idx
        finally:
            agent._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await agent._task


def test_note_panel_is_left_bar_with_icon() -> None:
    """Заметка — цитата с левой полосой цвета типа и иконкой в заголовке."""
    panel = _note_panel(Note("compact", "Контекст сжат", anchor=0))
    assert panel.title is not None and str(panel.title) == "⇄ сжатие"
    assert panel.border_style == "cyan"
    assert panel.box.mid_left == "│"  # левая полоса есть
    assert panel.box.top == " "  # верхней обводки нет — только полоса слева
    error = _note_panel(Note("error", "boom", anchor=0))
    assert str(error.title) == "✗ ошибка"
    assert error.border_style == "red"
