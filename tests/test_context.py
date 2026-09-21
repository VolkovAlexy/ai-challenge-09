"""ContextBuilder: сборка массива messages."""

from agent.core.context import FACTS_HEADER, ContextBuilder, apply_sliding_window
from agent.core.message import FunctionCall, Message, Role, ToolCall


def test_apply_sliding_window() -> None:
    history = [Message(role=Role.USER, content=str(i)) for i in range(5)]
    assert len(apply_sliding_window(history, 3)) == 3
    assert [m.content for m in apply_sliding_window(history, 3)] == ["2", "3", "4"]
    # n больше длины — вся история
    assert len(apply_sliding_window(history, 10)) == 5
    # n <= 0 — окно не применяется
    assert len(apply_sliding_window(history, 0)) == 5
    # копия, не ссылка
    windowed = apply_sliding_window(history, 2)
    windowed[0] = Message(role=Role.USER, content="x")
    assert history[-1].content == "4"


def test_facts_block_in_projection() -> None:
    builder = ContextBuilder()
    history = [Message(role=Role.USER, content="q")]
    messages = builder.build_messages(
        "SP", history, facts={"z_key": "1", "a_key": "2"}
    )
    assert [m.role for m in messages] == [Role.SYSTEM, Role.SYSTEM, Role.USER]
    assert messages[0].content == "SP"
    assert FACTS_HEADER in messages[1].content
    assert "- a_key: 2" in messages[1].content  # ключи отсортированы
    assert "- z_key: 1" in messages[1].content


def test_empty_facts_not_projected() -> None:
    builder = ContextBuilder()
    messages = builder.build_messages("SP", [], facts={})
    assert [m.role for m in messages] == [Role.SYSTEM]


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
