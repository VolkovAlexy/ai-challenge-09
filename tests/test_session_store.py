"""SessionStore (SQLite): снапшот, список, загрузка, удаление, экспорт, reopen."""

import time
from pathlib import Path

from agent.config.schema import AgentSettings, Config, validate_config
from agent.core.message import Message, Role
from agent.memory.persistence import SessionStore
from agent.memory.session import load_session


def make_config() -> Config:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": ["m1", "m9"]}
            },
            "default_model": "p1:m1",
        }
    )


def make_settings() -> AgentSettings:
    return AgentSettings.from_config(make_config())


def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    settings = make_settings()
    settings.model = "p1:m9"
    history = [
        Message(role=Role.USER, content="привет"),
        Message(role=Role.ASSISTANT, content="здравствуйте"),
    ]
    store.snapshot(sid, "chat", settings, "SP", history)

    data = store.get(sid)
    assert data is not None
    assert data.name == "chat"
    assert data.system_prompt == "SP"
    assert data.settings.model == "p1:m9"
    assert [m.content for m in data.history] == ["привет", "здравствуйте"]
    assert [m.role for m in data.history] == [Role.USER, Role.ASSISTANT]
    store.close()


def test_snapshot_upsert_not_duplicate(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    settings = make_settings()
    store.snapshot(sid, "a", settings, "SP", [Message(role=Role.USER, content="1")])
    store.snapshot(sid, "b", settings, "SP2", [Message(role=Role.USER, content="2")])

    data = store.get(sid)
    assert data is not None
    assert data.name == "b"
    assert data.system_prompt == "SP2"
    assert [m.content for m in data.history] == ["2"]
    assert store.count() == 1
    store.close()


def test_list_skips_empty_and_sorts_newest_first(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    settings = make_settings()
    second = store.new_id()
    first = store.new_id()
    store.snapshot(second, "second", settings, "SP", [])
    time.sleep(0.002)  # гарантируем разную updated_at для детерминированного порядка
    store.snapshot(first, "first", settings, "SP", [Message(role=Role.USER, content="x")])

    items = store.list()
    assert [i.id for i in items] == [first]  # пустая сессия скрыта
    store.close()


def test_list_title_from_first_user_message(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    history = [
        Message(role=Role.USER, content="  первая   строка\nвторая  "),
        Message(role=Role.ASSISTANT, content="ответ"),
        Message(role=Role.USER, content="второй вопрос"),
    ]
    store.snapshot(sid, "chat", make_settings(), "SP", history)

    items = store.list()
    assert items[0].title == "первая строка вторая"
    store.close()


def test_list_title_truncated(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    store.snapshot(sid, "chat", make_settings(), "SP", [Message(role=Role.USER, content="а" * 200)])

    title = store.list()[0].title
    assert title.endswith("…")
    assert len(title) <= 56
    store.close()


def test_list_title_fallback_to_name(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    store.snapshot(
        sid, "ручное имя", make_settings(), "SP", [Message(role=Role.ASSISTANT, content="ответ")]
    )

    items = store.list()
    assert items[0].title == "ручное имя"
    store.close()


def test_get_missing_returns_none(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    assert store.get("nope") is None
    store.close()


def test_delete(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    store.snapshot(sid, "a", make_settings(), "SP", [Message(role=Role.USER, content="x")])
    assert store.delete(sid) is True
    assert store.get(sid) is None
    assert store.delete(sid) is False
    store.close()


def test_export_to_jsonl(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "s.db")
    sid = store.new_id()
    settings = make_settings()
    settings.temperature = 0.2
    store.snapshot(sid, "exp", settings, "SP", [Message(role=Role.USER, content="hi")])

    out = tmp_path / "exp.jsonl"
    store.export(sid, out)
    data = load_session(out)
    assert data.name == "exp"
    assert data.settings.temperature == 0.2
    assert [m.content for m in data.history] == ["hi"]
    store.close()


def test_persist_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    store = SessionStore(path)
    sid = store.new_id()
    store.snapshot(sid, "a", make_settings(), "SP", [Message(role=Role.USER, content="x")])
    store.close()

    store2 = SessionStore(path)
    data = store2.get(sid)
    assert data is not None
    assert data.name == "a"
    assert [m.content for m in data.history] == ["x"]
    assert store2.list()[0].title == "x"
    store2.close()
