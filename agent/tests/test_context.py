"""ContextBuilder: сборка массива messages."""

from my_agent.core.context import ContextBuilder
from my_agent.core.message import FunctionCall, Message, Role, ToolCall


def test_system_and_history() -> None:
    builder = ContextBuilder()
    history = [
        Message(role=Role.USER, content="привет"),
        Message(role=Role.ASSISTANT, content="здравствуйте"),
    ]
    messages = builder.build_messages("ТЫ", history)
    assert [m.role for m in messages] == [Role.SYSTEM, Role.USER, Role.ASSISTANT]
    assert messages[0].content == "ТЫ"
    assert messages[1].content == "привет"


def test_empty_history() -> None:
    messages = ContextBuilder().build_messages("SP", [])
    assert messages == [Message(role=Role.SYSTEM, content="SP")]


def test_rag_chunks_and_memories_appended_before_history() -> None:
    history = [Message(role=Role.USER, content="q")]
    messages = ContextBuilder().build_messages(
        "SP", history, rag_chunks=["чанк1"], memories=["память1"]
    )
    assert len(messages) == 4
    assert "чанк1" in messages[1].content
    assert "память1" in messages[2].content
    assert messages[3].content == "q"


def test_tool_messages_roundtrip() -> None:
    history = [
        Message(
            role=Role.ASSISTANT,
            content=None,
            tool_calls=[ToolCall(id="c1", function=FunctionCall(name="get", arguments='{"a": 1}'))],
        ),
        Message(role=Role.TOOL, content="результат", tool_call_id="c1"),
    ]
    api = [m.to_api() for m in ContextBuilder().build_messages("SP", history)]
    assert api[1]["tool_calls"][0]["function"]["name"] == "get"
    assert api[2]["tool_call_id"] == "c1"
    assert "content" not in api[1] or api[1]["content"] is None


def test_to_api_omits_none_fields() -> None:
    data = Message(role=Role.USER, content="hi").to_api()
    assert data == {"role": "user", "content": "hi"}
