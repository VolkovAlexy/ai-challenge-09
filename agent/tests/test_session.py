"""Session save/load (jsonl): roundtrip настроек, истории, промпта, имени."""

import pytest

from my_agent.config.schema import AgentSettings, validate_config
from my_agent.core.agent import Agent
from my_agent.core.message import Message, Role
from my_agent.memory.session import (
    InMemorySession,
    SessionError,
    load_session,
)


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": ["m1", "m9"]}
            },
            "default_model": "p1:m1",
        }
    )


def make_agent(name: str = "chat-1") -> Agent:
    return Agent(
        name=name,
        settings=AgentSettings.from_config(make_config()),
        system_prompt="SP-TEST",
        llm=None,  # type: ignore[arg-type]  # save/load не ходят в LLM
        config=make_config(),  # type: ignore[arg-type]
    )


def test_save_load_roundtrip(tmp_path) -> None:
    agent = make_agent("my-chat")
    agent.memory.add(Message(role=Role.USER, content="привет"))
    agent.memory.add(Message(role=Role.ASSISTANT, content="здравствуйте"))
    agent.settings.model = "p1:m9"
    agent.settings.temperature = 0.3
    agent.settings.stop = ["END"]

    path = tmp_path / "sess.jsonl"
    agent.save(path)

    other = make_agent("other")
    other.load(path)
    assert other.name == "my-chat"
    assert other.settings.model == "p1:m9"
    assert other.settings.temperature == 0.3
    assert other.settings.stop == ["END"]
    assert other.system_prompt == "SP-TEST"
    assert [m.content for m in other.memory.history] == ["привет", "здравствуйте"]
    assert [m.role for m in other.memory.history] == [Role.USER, Role.ASSISTANT]


def test_save_creates_parent_dirs(tmp_path) -> None:
    agent = make_agent()
    path = tmp_path / "nested" / "dir" / "sess.jsonl"
    agent.save(path)
    assert path.exists()


def test_load_missing_file(tmp_path) -> None:
    with pytest.raises(SessionError, match="не найден"):
        load_session(tmp_path / "nope.jsonl")


def test_load_corrupted_file(tmp_path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"type": "meta"}', encoding="utf-8")
    with pytest.raises(SessionError):
        load_session(path)


def test_in_memory_session_basic() -> None:
    session = InMemorySession()
    assert len(session) == 0
    session.add(Message(role=Role.USER, content="x"))
    assert len(session) == 1
    snapshot = session.history
    session.add(Message(role=Role.ASSISTANT, content="y"))
    assert len(snapshot) == 1  # копия, не ссылка
    session.clear()
    assert len(session) == 0
