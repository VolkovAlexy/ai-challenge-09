"""MessageList: скролл длинного чата и авто-скролл к последнему сообщению."""

from textual.pilot import Pilot

from my_agent.config.schema import validate_config
from my_agent.core.message import Message
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp
from my_agent.ui.widgets.message_list import MessageList


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
