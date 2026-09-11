"""ChatInput: многострочный ввод — Shift+Enter, вставка (paste), отправка по Enter."""

import tempfile
from pathlib import Path

from my_agent.config.schema import validate_config
from my_agent.llm.client import LLMClient
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.app import AgentApp, ChatTab
from my_agent.ui.widgets.chat_input import ChatInput


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
        session_db=Path(tempfile.mkdtemp()) / "sessions.db",
    )


async def test_shift_enter_inserts_newline() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        chat_input.value = "line1"
        await pilot.pause()
        await pilot.press("shift+enter")
        await pilot.pause()
        assert chat_input.value == "line1\n"
        await pilot.press("l", "i", "n", "e", "2")
        await pilot.pause()
        assert chat_input.value == "line1\nline2"


async def test_multiline_paste() -> None:
    app = make_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        # реальный путь вставки: ctrl+v → TextArea.action_paste → буфер приложения
        app._clipboard = "line1\nline2"
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert chat_input.value == "line1\nline2"
        # высота подросла под две строки (+ отступ сверху и снизу)
        assert chat_input.styles.height.value == 4


async def test_enter_submits_multiline_and_clears() -> None:
    app = make_app()
    sent: list[str] = []

    def fake_handle_input(tab: ChatTab, text: str) -> bool:
        sent.append(text)
        return True

    app.handle_input = fake_handle_input  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInput)
        chat_input.focus()
        await pilot.pause()
        chat_input.value = "line1\nline2"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert sent == ["line1\nline2"]
        assert chat_input.value == ""
        assert chat_input.styles.height.value == 3
