"""Ветки диалога: InMemorySession, Agent-уровень, персистенция (jsonl + SQLite)."""

import asyncio
from pathlib import Path

import pytest

from agent.config.schema import AgentSettings, validate_config
from agent.core.agent import Agent
from agent.core.message import ChatChunk, Message, Role
from agent.memory.branching import BranchError, BranchState, normalize_branch_name
from agent.memory.persistence import SessionStore
from agent.memory.session import InMemorySession, load_session, save_session


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": ["m1"]}
            },
            "default_model": "p1:m1",
        }
    )


def make_agent() -> Agent:
    return Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),  # type: ignore[arg-type]
        system_prompt="SP",
        llm=None,  # type: ignore[arg-type]  # ветки не ходят в LLM
        config=make_config(),  # type: ignore[arg-type]
    )


# --- normalize_branch_name ---


def test_normalize_branch_name() -> None:
    assert normalize_branch_name("  Fix-1 ") == "fix-1"
    with pytest.raises(BranchError):
        normalize_branch_name("два слова")
    with pytest.raises(BranchError):
        normalize_branch_name("   ")


# --- InMemorySession: fork/switch ---


def test_fork_copies_state_and_switches() -> None:
    session = InMemorySession()
    session.add(Message(role=Role.USER, content="вопрос"))
    session.add(Message(role=Role.ASSISTANT, content="ответ"))
    session.compact_prefix(1, summary="сводка")
    session.facts = {"goal": "тест"}

    session.fork("b1")

    assert session.active_branch == "b1"
    assert len(session) == 2
    assert session.summary == "сводка"
    assert session.compacted_upto == 1
    assert session.facts == {"goal": "тест"}
    # старая ветка сохранена с тем же числом сообщений
    assert [name for name, _, active in session.branch_info()] == ["b1", "main"]


def test_branches_evolve_independently() -> None:
    session = InMemorySession()
    session.add(Message(role=Role.USER, content="q"))
    session.fork("a")
    session.add(Message(role=Role.ASSISTANT, content="ветка a"))
    session.facts = {"side": "a"}

    session.switch("main")

    assert [m.content for m in session.history] == ["q"]
    assert session.facts == {}
    assert session.active_branch == "main"

    session.switch("a")
    assert [m.content for m in session.history] == ["q", "ветка a"]
    assert session.facts == {"side": "a"}


def test_fork_duplicate_name_raises() -> None:
    session = InMemorySession()
    session.fork("x")
    session.switch("main")
    with pytest.raises(ValueError, match="уже существует"):
        session.fork("x")
    with pytest.raises(ValueError, match="уже существует"):
        session.fork("main")


def test_switch_missing_branch_raises() -> None:
    session = InMemorySession()
    with pytest.raises(KeyError):
        session.switch("нет-такой")


def test_switch_to_active_is_noop() -> None:
    session = InMemorySession()
    session.add(Message(role=Role.USER, content="q"))
    session.switch(session.active_branch)
    assert len(session) == 1


def test_clear_affects_only_active_branch() -> None:
    session = InMemorySession()
    session.add(Message(role=Role.USER, content="q1"))
    session.fork("b1")
    session.add(Message(role=Role.USER, content="q2"))

    session.clear()

    assert len(session) == 0
    assert session.summary is None and session.facts == {}
    session.switch("main")
    assert [m.content for m in session.history] == ["q1"]


def test_branch_info_counts_messages() -> None:
    session = InMemorySession()
    session.add(Message(role=Role.USER, content="q"))
    session.fork("b1")
    session.add(Message(role=Role.USER, content="q2"))
    info = {name: count for name, count, _ in session.branch_info()}
    assert info == {"b1": 2, "main": 1}


# --- Agent: fork/switch сбрасывают рантайм ---


def test_agent_fork_resets_runtime() -> None:
    agent = make_agent()
    agent.memory.add(Message(role=Role.USER, content="q"))
    agent.totals.in_tokens = 500

    previous = agent.fork_branch("fix")

    assert previous == "main"
    assert agent.memory.active_branch == "fix"
    assert agent.totals.in_tokens == 0


def test_agent_switch_restores_branch() -> None:
    agent = make_agent()
    agent.memory.add(Message(role=Role.USER, content="q"))
    agent.fork_branch("exp")
    agent.memory.add(Message(role=Role.USER, content="эксперимент"))

    agent.switch_branch("main")

    assert [m.content for m in agent.memory.history] == ["q"]
    with pytest.raises(KeyError):
        agent.switch_branch("нет-такой")


# --- персистенция: jsonl ---


def test_jsonl_roundtrip_with_branches_and_facts(tmp_path: Path) -> None:
    agent = make_agent()
    agent.memory.add(Message(role=Role.USER, content="q"))
    agent.memory.add(Message(role=Role.ASSISTANT, content="a"))
    agent.memory.compact_prefix(1, summary="сводка")
    agent.memory.facts = {"goal": "тест"}
    agent.fork_branch("b1")
    agent.memory.add(Message(role=Role.USER, content="в ветке"))
    agent.memory.facts = {"goal": "тест", "side": "b1"}

    path = save_session(
        tmp_path / "s.jsonl",
        settings=agent.settings,
        system_prompt=agent.system_prompt,
        name=agent.name,
        summary=agent.memory.summary,
        compacted_upto=agent.memory.compacted_upto,
        history=agent.memory.history,
        facts=agent.memory.facts,
        active_branch=agent.memory.active_branch,
        branches=agent.memory.branches,
    )
    data = load_session(path)

    assert data.active_branch == "b1"
    assert [m.content for m in data.history] == ["q", "a", "в ветке"]
    assert data.summary == "сводка"
    assert data.compacted_upto == 1
    assert data.facts == {"goal": "тест", "side": "b1"}
    assert set(data.branches) == {"main"}
    main = data.branches["main"]
    assert [m.content for m in main.history] == ["q", "a"]
    assert main.facts == {"goal": "тест"}
    assert main.summary == "сводка"


def test_jsonl_load_legacy_file_without_branches(tmp_path: Path) -> None:
    """Старый файл (без веток) загружается: активная ветка main."""
    path = tmp_path / "old.jsonl"
    meta = (
        '{"type": "meta", "name": "old", "system_prompt": "SP", '
        '"summary": null, "compacted_upto": 0, '
        '"agent": {"model": "p1:m1"}}'
    )
    line = (
        '{"type": "message", "message": {"role": "user", "content": "привет"}}'
    )
    path.write_text(f"{meta}\n{line}\n", encoding="utf-8")
    data = load_session(path)
    assert data.active_branch == "main"
    assert [m.content for m in data.history] == ["привет"]
    assert data.branches == {}


# --- персистенция: SQLite ---


def test_sqlite_roundtrip_with_branches_and_facts(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    settings = AgentSettings.from_config(make_config())  # type: ignore[arg-type]
    branch = BranchState(
        history=[Message(role=Role.USER, content="ветка")],
        summary=None,
        compacted_upto=0,
        facts={"side": "b"},
    )
    store.snapshot(
        sid,
        "chat",
        settings,
        "SP",
        [Message(role=Role.USER, content="активная")],
        summary="сводка",
        compacted_upto=1,
        facts={"goal": "тест"},
        active_branch="main",
        branches={"b1": branch},
    )

    data = store.get(sid)
    assert data is not None
    assert data.active_branch == "main"
    assert data.facts == {"goal": "тест"}
    assert data.summary == "сводка"
    assert set(data.branches) == {"b1"}
    assert [m.content for m in data.branches["b1"].history] == ["ветка"]
    assert data.branches["b1"].facts == {"side": "b"}

    # reopen store — миграции не ломают чтение
    store.close()
    store2 = SessionStore(tmp_path / "s.db")
    assert store2.get(sid) is not None
    store2.close()


# --- MockLLM-запрос отражает ветку: чат в b1 не видит main ---


class MockLLM:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls: list[object] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append(request)
        yield ChatChunk(content=self.answer)
        yield ChatChunk(finish_reason="stop")

    async def close(self) -> None:
        return None


def test_sliding_projection_uses_window_after_branch_switch() -> None:
    """В sliding-стратегии после переключения ветки LLM видит только её историю."""
    llm = MockLLM("ок")
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),  # type: ignore[arg-type]
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    agent.settings.context_strategy = "sliding"
    agent.settings.sliding_window = 1
    agent.memory.add(Message(role=Role.USER, content="q1"))
    agent.memory.add(Message(role=Role.ASSISTANT, content="a1"))
    agent.fork_branch("b1")

    asyncio.run(agent.ask("q2"))
    chat = llm.calls[0]
    user_contents = [m.content for m in chat.messages if m.role == Role.USER]
    # окно 1: в проекцию попадает только последнее сообщение ветки b1
    assert user_contents == ["q2"]
