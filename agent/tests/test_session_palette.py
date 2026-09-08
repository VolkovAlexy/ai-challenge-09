"""SessionPalette: /session открывает палитру; выбор загружает сессию в агента."""

import tempfile
from pathlib import Path

from textual.pilot import Pilot

from my_agent.config.schema import validate_config
from my_agent.core.agent import Agent
from my_agent.core.message import Message, Role
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp
from my_agent.ui.widgets.chat_input import ChatInput
from my_agent.ui.widgets.session_palette import SessionPalette


def make_app() -> AgentApp:
    config = validate_config(
        {
            "providers": {
                "alpha": {"api_base": "http://a/v1", "api_key": "", "models": ["m1", "m2"]},
            },
            "default_model": "alpha:m1",
        }
    )
    tmp = tempfile.mkdtemp()
    return AgentApp(
        config=config,
        llm=LLMClient(),
        tools=ToolRegistry(),
        default_system_prompt="SP",
        session_db=Path(tmp) / "sessions.db",
    )


def active_agent(app: AgentApp) -> Agent:
    agent = app.active_agent()
    assert agent is not None
    return agent


def seed_session(app: AgentApp, name: str, content: str) -> str:
    """Добавляет в store отдельную сессию с историей (чтобы её было что выбрать)."""
    agent = active_agent(app)
    sid = app._store.new_id()
    app._store.snapshot(
        sid,
        name,
        agent.settings,
        "SP-OTHER",
        [
            Message(role=Role.USER, content=content),
            Message(role=Role.ASSISTANT, content=content.upper()),
        ],
    )
    return sid


def seed_current(app: AgentApp, content: str = "текущая тема") -> None:
    """Даёт текущей сессии историю — иначе она скрыта из палитры (0 сообщений)."""
    agent = active_agent(app)
    tab = app.active_tab()
    assert tab is not None and tab.session_id is not None
    app._store.snapshot(
        tab.session_id,
        agent.name,
        agent.settings,
        agent.system_prompt,
        [Message(role=Role.USER, content=content)],
    )


async def open_session_palette(app: AgentApp, pilot: Pilot[None]) -> None:
    chat_input = app.query_one(ChatInput)
    chat_input.focus()
    await pilot.pause()
    chat_input.value = "/session"
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, SessionPalette)


async def test_session_opens_palette() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_session_palette(app, pilot)
        assert isinstance(app.screen, SessionPalette)


async def test_session_highlights_current() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        seed_current(app)
        await open_session_palette(app, pilot)
        palette = app.screen
        assert isinstance(palette, SessionPalette)
        highlighted = palette._list.highlighted_option
        assert highlighted is not None
        tab = app.active_tab()
        assert tab is not None
        assert highlighted.id == tab.session_id


async def test_session_empty_sessions_hidden() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        seed_session(app, "other", "привет")
        await open_session_palette(app, pilot)
        palette = app.screen
        assert isinstance(palette, SessionPalette)
        # стартовая пустая сессия скрыта; видна только засеянная
        assert palette._list.option_count == 1


async def test_session_palette_empty_state() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_session_palette(app, pilot)
        palette = app.screen
        assert isinstance(palette, SessionPalette)
        assert palette._list.option_count == 1
        assert palette._list.get_option_at_index(0).disabled  # "нет сессий"
        await pilot.press("enter")  # выбор невозможен — палитра остаётся открытой
        await pilot.pause()
        assert isinstance(app.screen, SessionPalette)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPalette)


async def test_session_select_loads_into_active() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        sid = seed_session(app, "other", "привет")
        await open_session_palette(app, pilot)
        # фильтр теперь по теме (первое сообщение пользователя)
        await pilot.press("п", "р", "и", "в")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPalette)
        agent = active_agent(app)
        assert agent.name == "other"
        assert agent.system_prompt == "SP-OTHER"
        assert [m.content for m in agent.memory.history] == ["привет", "ПРИВЕТ"]
        tab = app.active_tab()
        assert tab is not None
        assert tab.session_id == sid


async def test_session_select_current_is_noop() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        seed_current(app)
        current_name = active_agent(app).name
        await open_session_palette(app, pilot)
        await pilot.press("enter")  # единственная сессия — текущая
        await pilot.pause()
        assert not isinstance(app.screen, SessionPalette)
        assert active_agent(app).name == current_name
        tab = app.active_tab()
        assert tab is not None
        assert ("system", "Эта сессия уже открыта у активного агента.") in tab.notes


async def test_session_escape_cancels() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        seed_current(app)
        current_name = active_agent(app).name
        await open_session_palette(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPalette)
        assert active_agent(app).name == current_name
