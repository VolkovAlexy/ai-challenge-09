"""ModelPalette: /model без аргумента открывает палитру, выбор меняет модель."""

from textual.pilot import Pilot

from my_agent.config.schema import validate_config
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp
from my_agent.ui.widgets.chat_input import ChatInput
from my_agent.ui.widgets.model_palette import ModelPalette


def make_app() -> AgentApp:
    config = validate_config(
        {
            "providers": {
                "alpha": {"api_base": "http://a/v1", "api_key": "", "models": ["m1", "m2"]},
                "beta": {"api_base": "http://b/v1", "api_key": "", "models": ["m3"]},
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


async def open_palette(app: AgentApp, pilot: Pilot) -> None:
    chat_input = app.query_one(ChatInput)
    chat_input.focus()
    await pilot.pause()
    chat_input.value = "/model"
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    assert isinstance(app.screen, ModelPalette)


async def test_model_without_arg_opens_palette() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        assert isinstance(app.screen, ModelPalette)


async def test_palette_highlights_current_model() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        palette = app.screen
        highlighted = palette._list.highlighted_option
        assert highlighted is not None
        assert highlighted.id == "alpha:m1"


async def test_palette_filter_and_select() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("b", "e", "t", "a")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPalette)
        assert app.active_agent().settings.model == "beta:m3"
        tab = app.active_tab()
        assert tab is not None
        assert ("system", "Модель: beta:m3") in tab.notes


async def test_palette_arrows_and_enter() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPalette)
        assert app.active_agent().settings.model == "alpha:m2"


async def test_palette_escape_cancels() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ModelPalette)
        assert app.active_agent().settings.model == "alpha:m1"


async def test_palette_no_matches_enter_does_nothing() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        await open_palette(app, pilot)
        await pilot.press("z", "z", "z")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # палитра остаётся открытой, модель не поменялась
        assert isinstance(app.screen, ModelPalette)
        assert app.active_agent().settings.model == "alpha:m1"


async def test_tab_completion_keeps_command_prefix() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        chat_input.value = "/model "
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/model alpha:m1"


async def test_tab_completion_argument_prefix() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        chat_input.value = "/model a"
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert chat_input.value == "/model alpha:m"
