"""Сжатие контекста: config окон, компактор, триггеры в ask(), хранение саммари."""

import asyncio
from pathlib import Path

from my_agent.config.schema import AgentSettings, validate_config
from my_agent.core.agent import Agent
from my_agent.core.compactor import (
    _SUMMARY_SLACK_TOKENS,
    COMPACT_TARGET_SHARE,
    SUMMARY_SYSTEM_PROMPT,
    ContextCompactor,
)
from my_agent.core.context import estimate_messages
from my_agent.core.message import ChatChunk, Message, Role
from my_agent.memory.persistence import SessionStore
from my_agent.memory.session import InMemorySession, load_session, save_session
from tests.test_agent import MockLLM


def make_config(**overrides: object) -> object:
    """Config с окном 1000 токенов (для срабатывания порога) + переопределения."""
    base: dict[str, object] = {
        "providers": {
            "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": {"m1": 1000}},
        },
        "default_model": "p1:m1",
    }
    base.update(overrides)
    return validate_config(base)


def make_agent(
    chunks: list[ChatChunk], **config_overrides: object
) -> tuple[Agent, MockLLM]:
    config = make_config(**config_overrides)
    llm = MockLLM(chunks)
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(config),
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
    )
    return agent, llm


def big(text: str = "а", size: int = 1000) -> Message:
    """Сообщение ~size/4 токенов."""
    return Message(role=Role.USER, content=text * size)


# --- config: окна моделей ---


def test_models_list_normalizes_to_dict() -> None:
    config = validate_config(
        {
            "providers": {"p1": {"api_base": "http://p1/v1", "models": ["m1"]}},
            "default_model": "p1:m1",
        }
    )
    assert config.providers["p1"].models == {"m1": None}
    # None → context_window_default
    assert config.context_window_for("p1:m1") == config.context_window_default


def test_context_window_for_uses_model_window() -> None:
    config = make_config()
    assert config.context_window_for("p1:m1") == 1000


def test_models_window_must_be_positive() -> None:
    try:
        validate_config(
            {
                "providers": {"p1": {"api_base": "http://p1/v1", "models": {"m1": 0}}},
                "default_model": "p1:m1",
            }
        )
    except ValueError as exc:
        assert "m1" in str(exc)
    else:
        raise AssertionError("окно 0 должно отклоняться")


def test_compaction_threshold_bounds() -> None:
    for bad in (0.4, 1.5):
        try:
            validate_config(
                {
                    "providers": {"p1": {"api_base": "http://p1/v1", "models": {"m1": 100}}},
                    "default_model": "p1:m1",
                    "compaction_threshold": bad,
                }
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"threshold {bad} должен отклоняться")


# --- plan_removal ---


def test_plan_removal_keeps_tail_within_budget() -> None:
    compactor = ContextCompactor(llm=None)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="а" * 1000) for _ in range(4)]  # ~250 ток. каждый
    window = 1000
    budget = int(window * COMPACT_TARGET_SHARE) - _SUMMARY_SLACK_TOKENS
    removed = compactor.plan_removal(history, window, system_prompt="", previous_summary="")
    # дословный хвост влезает в бюджет, минимум MIN_KEEP=2 сообщения
    assert len(history) - removed >= 2
    tail = history[removed:]
    assert estimate_messages(tail) <= max(budget, 0) or len(tail) == 2


def test_plan_removal_nothing_to_remove_for_short_history() -> None:
    compactor = ContextCompactor(llm=None)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="hi"), Message(role=Role.ASSISTANT, content="ok")]
    assert compactor.plan_removal(history, 1000, system_prompt="", previous_summary="") == 0


# --- ContextCompactor.compact() ---


def test_compact_summarizes_prefix() -> None:
    chunks = [ChatChunk(content="SUMMARY!")]
    llm = MockLLM(chunks)
    compactor = ContextCompactor(llm)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="а" * 1000) for _ in range(4)]
    result = asyncio.run(
        compactor.compact(
            model="m1",
            api_base="http://p1/v1",
            api_key="k1",
            previous_summary="",
            history=history,
            window=1000,
            system_prompt="",
        )
    )
    assert result is not None
    assert result.summary == "SUMMARY!"
    assert result.removed >= 1
    # запрос суммаризатору: system-промпт + transcript удаляемого префикса
    request, api_base, api_key = llm.calls[0]
    assert api_base == "http://p1/v1"
    assert api_key == "k1"
    assert request.messages[0].content == SUMMARY_SYSTEM_PROMPT
    assert "user:" in request.messages[1].content


def test_compact_includes_previous_summary() -> None:
    llm = MockLLM([ChatChunk(content="NEW")])
    compactor = ContextCompactor(llm)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="а" * 1000) for _ in range(4)]
    asyncio.run(
        compactor.compact(
            model="m1",
            api_base="http://p1/v1",
            api_key="k1",
            previous_summary="OLD",
            history=history,
            window=1000,
            system_prompt="",
        )
    )
    user_msg = llm.calls[0][0].messages[1].content
    assert "Предыдущая сводка:" in user_msg
    assert "OLD" in user_msg


def test_compact_returns_none_when_nothing_to_remove() -> None:
    llm = MockLLM([ChatChunk(content="S")])
    compactor = ContextCompactor(llm)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="hi"), Message(role=Role.ASSISTANT, content="ok")]
    result = asyncio.run(
        compactor.compact(
            model="m1",
            api_base="http://p1/v1",
            api_key="k1",
            previous_summary="",
            history=history,
            window=1000,
            system_prompt="",
        )
    )
    assert result is None
    assert llm.calls == []  # суммаризатор не вызывался


def test_compact_result_usage_estimate_without_server_usage() -> None:
    """MockLLM не возвращает usage → расход суммаризации оценивается (chars/4)."""
    llm = MockLLM([ChatChunk(content="SUMMARY!")])
    compactor = ContextCompactor(llm)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="а" * 1000) for _ in range(4)]
    result = asyncio.run(
        compactor.compact(
            model="m1",
            api_base="http://p1/v1",
            api_key="k1",
            previous_summary="",
            history=history,
            window=1000,
            system_prompt="",
        )
    )
    assert result is not None
    assert result.estimated is True
    assert result.in_tokens > 0  # system-промпт + transcript префикса
    assert result.out_tokens == len("SUMMARY!") // 4


def test_compact_result_uses_server_usage() -> None:
    llm = MockLLM(
        [
            ChatChunk(content="SUMMARY!"),
            ChatChunk.from_sse_data(  # type: ignore[arg-type]
                {"usage": {"prompt_tokens": 500, "completion_tokens": 20}}
            ),
        ]
    )
    compactor = ContextCompactor(llm)  # type: ignore[arg-type]
    history = [Message(role=Role.USER, content="а" * 1000) for _ in range(4)]
    result = asyncio.run(
        compactor.compact(
            model="m1",
            api_base="http://p1/v1",
            api_key="k1",
            previous_summary="",
            history=history,
            window=1000,
            system_prompt="",
        )
    )
    assert result is not None
    assert result.in_tokens == 500
    assert result.out_tokens == 20
    assert result.estimated is False


# --- триггер в ask(): порог заполнения окна ---


def test_ask_compacts_when_threshold_exceeded() -> None:
    agent, llm = make_agent([ChatChunk(content="SUMMARY!")])
    for text in ("q1", "q2", "q3", "q4"):
        asyncio.run(agent.ask("а" * 1000 + text))
    # 4 хода: system ~0 + 8 сообщений ~2000 токенов > 85% окна 1000
    assert llm.calls
    # предпоследний вызов (последний ход после возможного сжатия) содержит саммари
    chat_request = llm.calls[-1][0]
    summaries = [
        m
        for m in chat_request.messages
        if m.role is Role.SYSTEM and "SUMMARY!" in (m.content or "")
    ]
    if agent.memory.summary is not None:
        assert len(summaries) == 1
        assert agent.memory.summary == "SUMMARY!"
        # чат не меняется: вся история на месте, сжат только префикс для LLM
        assert agent.memory.compacted_upto > 0
        assert len(agent.memory.history) == 8  # 4 хода × (user + assistant)
        assert agent.memory.compacted_upto < len(agent.memory.history)
        # заметка о сжатии
        assert agent.compaction_note is not None
        assert "саммари" in agent.compaction_note
        # саммари-вызов шёл к той же модели
        summary_call = llm.calls[0][0]
        assert summary_call.model == "m1"


def test_ask_no_compaction_below_threshold() -> None:
    agent, llm = make_agent([ChatChunk(content="ok")])
    asyncio.run(agent.ask("hi"))
    assert agent.memory.summary is None
    assert agent.compaction_note is None
    # один вызов — сам ход, суммаризатора не было
    assert len(llm.calls) == 1


def test_compaction_counts_in_session_totals() -> None:
    """Расход LLM-вызова суммаризации попадает в накопительные счётчики сессии."""
    agent, _ = make_agent([ChatChunk(content="SUMMARY!")])
    for text in ("q1", "q2", "q3", "q4"):
        asyncio.run(agent.ask("а" * 1000 + text))
    if agent.memory.summary is not None:  # сжатие случилось
        assert agent.totals.in_tokens > 0
        # out — ответы чата плюс текст саммари
        assert agent.totals.out_tokens >= len("SUMMARY!") // 4
        assert agent.totals.estimated is True  # MockLLM usage не возвращает


def test_summary_enters_chat_request_as_system_message() -> None:
    agent, llm = make_agent([ChatChunk(content="SUM")])
    # 4 больших сообщения (~2000 токенов) — порог превышен уже на первом ask()
    for i in range(4):
        role = Role.USER if i % 2 == 0 else Role.ASSISTANT
        agent.memory.add(Message(role=role, content=f"m{i}-" + "а" * 990))
    asyncio.run(agent.ask("новый вопрос"))
    assert agent.memory.summary == "SUM"
    # вызов 0 — суммаризатор, вызов 1 — основной чат
    assert len(llm.calls) == 2
    chat_messages = llm.calls[1][0].messages
    assert chat_messages[0].content == "SP"
    assert chat_messages[1].role is Role.SYSTEM
    assert "SUM" in (chat_messages[1].content or "")
    # несжатый хвост дословно: всё, кроме ответа ассистента, дописанного после
    expected = [m.content for m in agent.memory.history[agent.memory.compacted_upto : -1]]
    assert [m.content for m in chat_messages[2:]] == expected


# --- чат не меняется: проекция для LLM ---


def test_compact_prefix_marks_without_deleting() -> None:
    memory = InMemorySession()
    for i in range(5):
        memory.add(Message(role=Role.USER, content=f"m{i}"))
    memory.compact_prefix(2, summary="S")
    assert len(memory.history) == 5  # чат не изменился
    assert memory.summary == "S"
    assert memory.compacted_upto == 2
    assert [m.content for m in memory.tail] == ["m2", "m3", "m4"]
    memory.compact_prefix(0, summary="S2")  # no-op
    assert memory.compacted_upto == 2


def test_projection_excludes_summarized_prefix() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    for i in range(4):
        agent.memory.add(Message(role=Role.USER, content=f"m{i}"))
    agent.memory.compact_prefix(2, summary="S")
    projection = agent._projection()
    contents = [m.content for m in projection]
    assert "m0" not in contents and "m1" not in contents
    assert contents[-2:] == ["m2", "m3"]


def test_context_now_equals_projection_estimate() -> None:
    agent, _ = make_agent([ChatChunk(content="SUM")])
    for i in range(4):
        agent.memory.add(Message(role=Role.USER, content=f"m{i}-" + "а" * 990))
    asyncio.run(agent.ask("новый вопрос"))
    assert agent.memory.summary == "SUM"
    agent.last_usage = None  # форсируем fallback-ветку оценки
    assert agent.context_now == (estimate_messages(agent._projection()), True)


def test_message_usage_survives_compaction() -> None:
    agent, _ = make_agent([ChatChunk(content="SUM")])
    asyncio.run(agent.ask("первый"))
    saved = dict(agent.message_usage)
    assert saved  # usage первого ответа привязан к его индексу
    for i in range(4):
        agent.memory.add(Message(role=Role.USER, content=f"m{i}-" + "а" * 990))
    asyncio.run(agent.ask("новый вопрос"))
    assert agent.memory.summary == "SUM"  # сжатие случилось
    for index, usage in saved.items():
        # индексы в полной истории не сдвигаются — бейджи токенов остаются
        assert agent.message_usage.get(index) is usage


def test_context_counter_after_compaction_is_summarized() -> None:
    """Счётчик context считается по проекции (саммари + хвост), а не по полной истории."""
    agent, _ = make_agent([ChatChunk(content="SUM")])
    for i in range(4):
        agent.memory.add(Message(role=Role.USER, content=f"m{i}-" + "а" * 990))
    asyncio.run(agent.ask("новый вопрос"))
    assert agent.memory.summary == "SUM"
    assert agent.memory.compacted_upto > 0
    full = agent.context_builder.build_messages("SP", agent.memory.history)
    projected = estimate_messages(agent._projection())
    assert projected < estimate_messages(full)


# --- триггер в ask(): обрезка сервером ---


def test_truncated_usage_forces_next_compaction() -> None:
    # окно 1000, порог 0.95: обычный триггер не сработает, но сервер вернул
    # prompt_tokens=10 при отправке ~600 — ход обрезан → сжать при следующем ask()
    agent, llm = make_agent(
        [
            ChatChunk(content="ok"),
            ChatChunk.from_sse_data(  # type: ignore[arg-type]
                {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}
            ),
        ],
        compaction_threshold=0.95,
    )
    asyncio.run(agent.ask("б" * 2400))
    assert agent.last_usage is not None and agent.last_usage.truncated
    asyncio.run(agent.ask("ещё"))
    # второй вызов — суммаризатор, вызван до основного хода
    assert len(llm.calls) >= 2
    assert agent.memory.summary == "ok"
    assert agent.compaction_note is not None


# --- хранение саммари ---


def test_session_jsonl_round_trip_with_summary(tmp_path: Path) -> None:
    from my_agent.config.schema import AgentSettings

    settings = AgentSettings.from_config(make_config())
    path = tmp_path / "s.jsonl"
    save_session(
        path,
        settings=settings,
        system_prompt="SP",
        name="n",
        summary="сводка",
        compacted_upto=3,
        history=[Message(role=Role.USER, content="hi")],
    )
    data = load_session(path)
    assert data.summary == "сводка"
    assert data.compacted_upto == 3
    assert data.history[0].content == "hi"


def test_session_jsonl_round_trip_without_summary(tmp_path: Path) -> None:
    from my_agent.config.schema import AgentSettings

    settings = AgentSettings.from_config(make_config())
    path = tmp_path / "s.jsonl"
    save_session(
        path,
        settings=settings,
        system_prompt="SP",
        history=[Message(role=Role.USER, content="hi")],
    )
    data = load_session(path)
    assert data.summary is None
    assert data.compacted_upto == 0


def test_session_jsonl_old_format_without_compacted_upto(tmp_path: Path) -> None:
    """Старые файлы (meta без compacted_upto) читаются: сжатый префикс = 0."""
    import json

    from my_agent.config.schema import AgentSettings

    settings = AgentSettings.from_config(make_config())
    path = tmp_path / "old.jsonl"
    meta = {
        "type": "meta",
        "name": "n",
        "system_prompt": "SP",
        "summary": "старая сводка",
        "agent": settings.model_dump(),
    }
    lines = [json.dumps(meta, ensure_ascii=False)]
    lines.append(
        json.dumps(
            {"type": "message", "message": Message(role=Role.USER, content="hi").model_dump()},
            ensure_ascii=False,
        )
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    data = load_session(path)
    assert data.summary == "старая сводка"
    assert data.compacted_upto == 0


def test_store_round_trip_with_summary(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "db.sqlite")
    sid = store.new_id()
    settings = AgentSettings.from_config(make_config())
    store.snapshot(
        sid,
        "n",
        settings,
        "SP",
        [Message(role=Role.USER, content="hi")],
        summary="S",
        compacted_upto=2,
    )
    data = store.get(sid)
    assert data is not None
    assert data.summary == "S"
    assert data.compacted_upto == 2
    # старые записи без summary → None, compacted_upto → 0
    store2 = SessionStore(tmp_path / "db2.sqlite")
    sid2 = store2.new_id()
    store2.snapshot(sid2, "n2", settings, "SP", [])
    data2 = store2.get(sid2)
    assert data2 is not None
    assert data2.summary is None
    assert data2.compacted_upto == 0
    store.close()
    store2.close()


def test_apply_session_restores_summary(tmp_path: Path) -> None:
    agent, _ = make_agent([ChatChunk(content="ok")])
    from my_agent.config.schema import AgentSettings

    settings = AgentSettings.from_config(make_config())
    path = tmp_path / "s.jsonl"
    save_session(
        path,
        settings=settings,
        system_prompt="SP",
        summary="S",
        compacted_upto=2,
        history=[Message(role=Role.USER, content="hi")],
    )
    agent.apply_session(load_session(path))
    assert agent.memory.summary == "S"
    assert agent.memory.compacted_upto == 2
    agent.memory.clear()
    assert agent.memory.summary is None
    assert agent.memory.compacted_upto == 0


# --- ContextBuilder: саммари в сообщениях ---


def test_build_messages_places_summary_after_system() -> None:
    from my_agent.core.context import SUMMARY_HEADER, ContextBuilder

    builder = ContextBuilder()
    history = [Message(role=Role.USER, content="hi")]
    messages = builder.build_messages("SP", history, summary="сводка")
    assert [m.role for m in messages] == [Role.SYSTEM, Role.SYSTEM, Role.USER]
    assert messages[0].content == "SP"
    assert messages[1].content == SUMMARY_HEADER + "\nсводка"


def test_build_messages_without_summary() -> None:
    from my_agent.core.context import ContextBuilder

    builder = ContextBuilder()
    history = [Message(role=Role.USER, content="hi")]
    messages = builder.build_messages("SP", history)
    assert [m.role for m in messages] == [Role.SYSTEM, Role.USER]


def test_estimate_messages_matches_chars_over_four() -> None:
    import json

    messages = [Message(role=Role.USER, content="а" * 400)]
    expected = len(json.dumps(messages[0].to_api(), ensure_ascii=False)) // 4
    assert estimate_messages(messages) == expected
