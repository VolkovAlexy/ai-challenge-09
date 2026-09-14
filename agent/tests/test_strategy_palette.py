"""StrategyPalette: /strategy без аргумента открывает палитру, выбор меняет стратегию."""

import tempfile
from pathlib import Path

from textual.pilot import Pilot

from my_agent.config.schema import validate_config
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp
from my_agent.ui.widgets.chat_input import ChatInput
from my_agent.ui.widgets.strategy_palette import StrategyPalette


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
        session_db=Path(tempfile.mkdtemp()) / "sessions.db",
    )


async def open_palette(app: AgentApp, pilot: Pilot) -> None:
    chat_input = app.query_one(ChatInput)
    chat_input.focus()
    await pilot.pause()
    chat_input.value = "/strategy"
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def test_strategy_without_arg_opens_palette() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        assert isinstance(app.screen, StrategyPalette)


async def test_palette_lists_all_strategies_and_highlights_current() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        palette = app.screen
        assert palette._list.option_count == 4
        highlighted = palette._list.highlighted_option
        assert highlighted is not None
        assert highlighted.id == "summary"
        assert str(highlighted.prompt).startswith("* summary")


async def test_palette_escape_cancels() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, StrategyPalette)
        assert app.active_agent().settings.context_strategy == "summary"


async def test_palette_pick_changes_strategy_and_notes() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("down")  # summary → sliding
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, StrategyPalette)
        assert app.active_agent().settings.context_strategy == "sliding"
        tab = app.active_tab()
        assert tab is not None
        assert any("Стратегия контекста: sliding" in n.text for n in tab.notes)


async def test_typed_argument_still_works() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        chat_input.value = "/strategy facts"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, StrategyPalette)
        assert app.active_agent().settings.context_strategy == "facts"
