"""Сессионная память: jsonl-экспорт/загрузка + InMemorySession."""

from pathlib import Path

import pytest

from my_agent.config.schema import AgentSettings, Config, validate_config
from my_agent.core.agent import Agent
from my_agent.core.message import Message, Role
from my_agent.memory.session import (
    InMemorySession,
    SessionError,
    load_session,
    save_session,
)


def make_config() -> Config:
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
        llm=None,  # type: ignore[arg-type]  # export не ходит в LLM
        config=make_config(),
    )


def test_export_load_roundtrip(tmp_path: Path) -> None:
    agent = make_agent("my-chat")
    agent.memory.add(Message(role=Role.USER, content="привет"))
    agent.memory.add(
        Message(role=Role.ASSISTANT, content="здравствуйте", reasoning="мысленный процесс")
    )
    agent.settings.model = "p1:m9"
    agent.settings.temperature = 0.3
    agent.settings.stop = ["END"]

    path = agent.export(tmp_path / "sess.jsonl")

    data = load_session(path)
    assert data.name == "my-chat"
    assert data.settings.model == "p1:m9"
    assert data.settings.temperature == 0.3
    assert data.settings.stop == ["END"]
    assert data.system_prompt == "SP-TEST"
    assert [m.content for m in data.history] == ["привет", "здравствуйте"]
    assert [m.role for m in data.history] == [Role.USER, Role.ASSISTANT]
    assert data.history[1].reasoning == "мысленный процесс"


def test_export_creates_parent_dirs(tmp_path: Path) -> None:
    agent = make_agent()
    path = agent.export(tmp_path / "nested" / "dir" / "sess.jsonl")
    assert path.exists()


def test_save_session_direct(tmp_path: Path) -> None:
    path = save_session(
        tmp_path / "direct.jsonl",
        settings=AgentSettings.from_config(make_config()),
        system_prompt="SP",
        name="n",
        history=[Message(role=Role.USER, content="x")],
    )
    assert path.exists()
    assert load_session(path).name == "n"


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SessionError, match="не найден"):
        load_session(tmp_path / "nope.jsonl")


def test_load_corrupted_file(tmp_path: Path) -> None:
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
