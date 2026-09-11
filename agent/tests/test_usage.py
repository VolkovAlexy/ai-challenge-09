"""Подсчёт токенов: парсинг usage из SSE, фолбэк-оценка, Σ за сессию, StatusBar."""

import asyncio

import pytest
from rich.console import Group
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from my_agent.config.schema import AgentSettings, validate_config
from my_agent.core.agent import Agent
from my_agent.core.context import fmt_tokens
from my_agent.core.message import ChatChunk, ChatRequest, Message, Role
from my_agent.llm import client as llm_client_module
from my_agent.llm.client import LLMClient, LLMError
from my_agent.memory.session import SessionData
from my_agent.ui.app import ChatTab
from my_agent.ui.colors import ANSWER_STYLE, REASONING_STYLE, TOKENS_STYLE, USER_BUBBLE_BG
from my_agent.ui.widgets.message_list import BUBBLE_BOX, REASONING_BOX, MessageList
from my_agent.ui.widgets.status_bar import StatusBar, context_part, totals_part


class MockLLM:
    """Мок LLMClient: отдаёт заданные чанки, запоминает параметры запроса."""

    def __init__(self, chunks: list[ChatChunk], delay: float = 0.0) -> None:
        self.chunks = chunks
        self.delay = delay

    async def astream(self, request: object, api_base: str, api_key: str):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk

    async def close(self) -> None:
        return None


def make_config() -> object:
    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": ["m1"]},
            },
            "default_model": "p1:m1",
        }
    )


def make_agent(chunks: list[ChatChunk], delay: float = 0.0) -> tuple[Agent, MockLLM]:
    llm = MockLLM(chunks, delay=delay)
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    return agent, llm


# --- парсинг usage в message.py ---


def test_usage_chunk_with_empty_choices() -> None:
    """Финальный чанк стрима: пустой choices + usage — не теряем его."""
    chunk = ChatChunk.from_sse_data({"usage": {"prompt_tokens": 120, "completion_tokens": 45}})
    assert chunk is not None
    assert chunk.usage is not None
    assert chunk.usage.prompt_tokens == 120
    assert chunk.usage.completion_tokens == 45
    assert chunk.content is None


def test_usage_parses_reasoning_tokens() -> None:
    """Thinking-модели: completion_tokens_details.reasoning_tokens — доля размышлений."""
    chunk = ChatChunk.from_sse_data(
        {
            "usage": {
                "prompt_tokens": 28,
                "completion_tokens": 3251,
                "completion_tokens_details": {"reasoning_tokens": 3233},
            }
        }
    )
    assert chunk is not None
    assert chunk.usage is not None
    assert chunk.usage.prompt_tokens == 28
    assert chunk.usage.completion_tokens == 3251
    assert chunk.usage.reasoning_tokens == 3233
    assert chunk.usage.total_tokens == 3279

    # без details / без reasoning_tokens — 0, не ошибка
    chunk = ChatChunk.from_sse_data({"usage": {"prompt_tokens": 1, "completion_tokens": 2}})
    assert chunk is not None and chunk.usage is not None
    assert chunk.usage.reasoning_tokens == 0
    chunk = ChatChunk.from_sse_data(
        {
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": "x"},
            }
        }
    )
    assert chunk is not None and chunk.usage is not None
    assert chunk.usage.reasoning_tokens == 0


def test_usage_attached_to_normal_chunk() -> None:
    chunk = ChatChunk.from_sse_data(
        {
            "choices": [{"delta": {"content": "ok"}, "finish_reason": None}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
    )
    assert chunk is not None
    assert chunk.usage is not None
    assert chunk.usage.total_tokens == 12


def test_empty_choices_without_usage_is_dropped() -> None:
    assert ChatChunk.from_sse_data({"choices": []}) is None
    assert ChatChunk.from_sse_data({}) is None


def test_usage_with_non_int_fields_is_ignored() -> None:
    chunk = ChatChunk.from_sse_data({"usage": {"prompt_tokens": "x"}, "choices": []})
    assert chunk is None


def test_error_chunk_parsed_from_sse() -> None:
    """Ошибка провайдера в HTTP 200 стриме: {"error": {"message": ...}} или строка."""
    chunk = ChatChunk.from_sse_data({"error": {"message": "context overflow", "code": 400}})
    assert chunk is not None
    assert chunk.error == "context overflow"
    assert chunk.content is None

    chunk = ChatChunk.from_sse_data({"error": "prompt too long"})
    assert chunk is not None
    assert chunk.error == "prompt too long"

    # null/пустышки — не ошибка
    assert ChatChunk.from_sse_data({"error": None}) is None
    assert ChatChunk.from_sse_data({"error": ""}) is None
    chunk = ChatChunk.from_sse_data({"choices": [{"delta": {"content": "ok"}}]})
    assert chunk is not None
    assert chunk.error is None


def test_reasoning_delta_parsed() -> None:
    """Thinking-модели: GLM шлёт 'reasoning', DeepSeek-совместимые — 'reasoning_content'."""
    chunk = ChatChunk.from_sse_data(
        {"choices": [{"delta": {"reasoning": "думаю…"}, "finish_reason": None}]}
    )
    assert chunk is not None
    assert chunk.reasoning == "думаю…"
    assert chunk.content is None

    chunk = ChatChunk.from_sse_data({"choices": [{"delta": {"reasoning_content": "мысленный"}}]})
    assert chunk is not None
    assert chunk.reasoning == "мысленный"

    # без reasoning — None
    chunk = ChatChunk.from_sse_data({"choices": [{"delta": {"content": "ok"}}]})
    assert chunk is not None
    assert chunk.reasoning is None


def test_request_body_includes_stream_options() -> None:
    request = ChatRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    body = request.to_body()
    assert body["stream_options"] == {"include_usage": True}


# --- ретрай без stream_options при 400 ---


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = '{"error": {"message": "stream_options is not supported"}}'

    async def aread(self) -> None:
        return None


class FakeEvent:
    def __init__(self, data: str) -> None:
        self.data = data


class FakeEventSource:
    def __init__(self, response: FakeResponse, events: list[FakeEvent]) -> None:
        self.response = response
        self._events = events

    async def aiter_sse(self):
        for event in self._events:
            yield event


class FakeConnect:
    """Подменяет aconnect_sse: последовательность исходов, запоминает тела."""

    def __init__(self, outcomes: list[tuple[FakeResponse, list[FakeEvent]]]) -> None:
        self.outcomes = outcomes
        self.bodies: list[dict] = []

    def __call__(self, client: object, method: str, url: str, json: dict, headers: dict):
        self.bodies.append({**json})  # копия: клиент мутирует тело (pop stream_options)
        return self

    async def __aenter__(self) -> FakeEventSource:
        outcome = self.outcomes.pop(0)
        return FakeEventSource(*outcome)

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_retry_without_stream_options_on_400(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConnect(
        [
            (FakeResponse(400), []),
            (
                FakeResponse(200),
                [
                    FakeEvent('{"choices": [{"delta": {"content": "ok"}}]}'),
                    FakeEvent("[DONE]"),
                ],
            ),
        ]
    )
    monkeypatch.setattr(llm_client_module, "aconnect_sse", fake)
    llm = LLMClient()
    request = ChatRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    chunks = [c async for c in llm.astream(request, "http://x/v1", "k")]
    await llm.close()
    assert [c.content for c in chunks] == ["ok"]
    assert "stream_options" in fake.bodies[0]
    assert "stream_options" not in fake.bodies[1]


async def test_client_raises_on_sse_error_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSE event: error (HTTP 200) → LLMError с текстом от провайдера."""
    fake = FakeConnect(
        [
            (
                FakeResponse(200),
                [
                    FakeEvent('{"choices": [{"delta": {"content": "часть"}}]}'),
                    FakeEvent('{"error": {"message": "prompt is too long"}}'),
                    FakeEvent("[DONE]"),
                ],
            ),
        ]
    )
    monkeypatch.setattr(llm_client_module, "aconnect_sse", fake)
    llm = LLMClient()
    request = ChatRequest(model="m", messages=[Message(role=Role.USER, content="hi")])
    with pytest.raises(LLMError, match="prompt is too long"):
        async for _ in llm.astream(request, "http://x/v1", "k"):
            pass
    await llm.close()


# --- учёт в агенте ---


def test_usage_from_server() -> None:
    chunks = [
        ChatChunk(content="Ответ"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.last_usage.prompt_tokens == 100
    assert agent.last_usage.completion_tokens == 50
    assert agent.last_usage.estimated is False
    # токены хода привязаны к assistant-сообщению (индекс 1 в истории)
    assert agent.message_usage[1] is agent.last_usage


def test_usage_from_server_counts_reasoning() -> None:
    """completion_tokens у API включает размышления; reasoning выделяется из details.

    out статус-бара — весь вывод (ответ + размышления), answer_tokens — видимый
    ответ (только он остаётся в контексте: reasoning в API не переотправляется).
    """
    chunks = [
        ChatChunk(reasoning="думаю…"),
        ChatChunk(content="Ответ"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "completion_tokens_details": {"reasoning_tokens": 45},
                }
            }
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.last_usage.completion_tokens == 50
    assert agent.last_usage.reasoning_tokens == 45
    assert agent.last_usage.answer_tokens == 5
    assert agent.totals.out_tokens == 50  # весь вывод, включая размышления
    # видимый ответ — 5 токенов размышлений + 45 из details не «съедают» его
    tokens, estimated = agent.context_now
    assert estimated is False
    assert tokens == 100 + 5  # prompt + видимый ответ


def test_usage_fallback_estimate() -> None:
    chunks = [ChatChunk(content="Привет мир"), ChatChunk(finish_reason="stop")]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.last_usage.estimated is True
    assert agent.last_usage.completion_tokens == len("Привет мир") // 4
    assert agent.last_usage.prompt_tokens > 0
    assert agent.message_usage[1] is agent.last_usage


def test_usage_fallback_estimate_counts_reasoning() -> None:
    """API не вернул usage: размышления тоже оцениваются chars/4 (не теряются)."""
    reasoning = "раз" * 40  # 120 символов → ~30 токенов
    chunks = [
        ChatChunk(reasoning=reasoning),
        ChatChunk(content="Привет мир"),
        ChatChunk(finish_reason="stop"),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.last_usage.estimated is True
    assert agent.last_usage.reasoning_tokens == len(reasoning) // 4
    assert agent.last_usage.completion_tokens == len("Привет мир") // 4 + len(reasoning) // 4
    assert agent.last_usage.answer_tokens == len("Привет мир") // 4


def test_message_usage_per_turn() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("one"))
    asyncio.run(agent.ask("two"))
    # два хода → два assistant-сообщения (индексы 1 и 3) со своими usage
    assert sorted(agent.message_usage) == [1, 3]
    assert agent.message_usage[1].completion_tokens == 50
    assert agent.message_usage[3].completion_tokens == 50
    assert agent.message_usage[3].prompt_tokens >= agent.message_usage[1].prompt_tokens


# --- накопительные счётчики сессии (in/out/Σ) ---


def test_totals_fresh_agent_is_zero() -> None:
    agent, _ = make_agent([])
    assert agent.totals.in_tokens == 0
    assert agent.totals.out_tokens == 0
    assert agent.totals.total_tokens == 0
    assert agent.totals.estimated is False


def test_totals_accumulate_over_turns() -> None:
    """in = Σ prompt_tokens всех запросов (контекст переотправляется целиком)."""
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("one"))
    asyncio.run(agent.ask("two"))
    assert agent.totals.in_tokens == 200
    assert agent.totals.out_tokens == 100
    assert agent.totals.total_tokens == 300
    assert agent.totals.estimated is False


def test_totals_marked_estimated_without_server_usage() -> None:
    agent, _ = make_agent([ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.totals.estimated is True
    assert agent.totals.in_tokens == agent.last_usage.prompt_tokens
    assert agent.totals.out_tokens == len("Привет мир") // 4


def test_failed_turn_not_counted_in_totals() -> None:
    class FailingLLM:
        async def astream(self, request: object, api_base: str, api_key: str):
            raise LLMError("HTTP 401: invalid api key", status=401)
            yield

        async def close(self) -> None:
            return None

    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, good_llm = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.totals.total_tokens == 150
    agent._llm = FailingLLM()  # type: ignore[assignment]
    with pytest.raises(LLMError, match="401"):
        asyncio.run(agent.ask("again"))
    assert agent.totals.total_tokens == 150  # неудавшийся ход не посчитан
    del good_llm


def test_cancel_counts_partial_in_totals() -> None:
    chunks = [ChatChunk(content="частичный ответ"), ChatChunk(content="x")]
    agent, _ = make_agent(chunks, delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.1)  # первый чанк (0.05) уже доставлен
        assert agent.cancel_ask() is True
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert agent.totals.estimated is True
    assert agent.totals.out_tokens == len("частичный ответ") // 4
    assert agent.last_usage is not None
    assert agent.totals.in_tokens == agent.last_usage.prompt_tokens


def test_apply_session_resets_totals() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.totals.total_tokens == 150
    data = SessionData(settings=agent.settings, system_prompt="SP", name="test")
    agent.apply_session(data)
    assert agent.totals.total_tokens == 0
    assert agent.totals.estimated is False


def test_reset_totals() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    agent.reset_totals()
    assert agent.totals.total_tokens == 0
    assert agent.totals.estimated is False


# --- детект обрезки контекста (server usage << отправленный промпт) ---


def test_truncation_detected_when_server_usage_much_smaller() -> None:
    """Сервер обработал заметно меньше токенов, чем отправлено → truncated."""
    long_text = "б" * 4000  # ~1000 токенов по локальной оценке chars/4
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 5}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask(long_text))
    assert agent.last_usage is not None
    assert agent.last_usage.truncated is True


def test_no_truncation_flag_when_usage_matches() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 5}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    assert agent.last_usage.truncated is False


async def test_live_estimate_during_streaming() -> None:
    chunks = [
        ChatChunk(content="часть "),
        ChatChunk(content="ответа"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 30, "completion_tokens": 8}}
        ),
    ]
    agent, _ = make_agent(chunks, delay=0.05)
    task = agent.start_ask("hi")
    await asyncio.sleep(0.02)  # до первого чанка: «думает» + живая оценка контекста
    assert agent.is_thinking is True
    assert agent.last_usage is not None
    assert agent.last_usage.estimated is True
    assert agent.last_usage.prompt_tokens > 0
    await task
    assert agent.is_thinking is False
    assert agent.last_usage is not None
    assert agent.last_usage.estimated is False
    assert agent.last_usage.completion_tokens == 8


def test_cancel_counts_partial_estimate() -> None:
    chunks = [ChatChunk(content="частичный ответ"), ChatChunk(content="x")]
    agent, _ = make_agent(chunks, delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.1)  # первый чанк (0.05) уже доставлен
        assert agent.cancel_ask() is True
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert agent.last_usage is not None
    assert agent.last_usage.estimated is True
    assert agent.last_usage.completion_tokens == len("частичный ответ") // 4
    # частичный ответ сохранён в истории → запись usage появилась
    assert agent.message_usage[1] is agent.last_usage


def test_error_restores_previous_usage() -> None:
    class FailingLLM:
        async def astream(self, request: object, api_base: str, api_key: str):
            raise LLMError("HTTP 401: invalid api key", status=401)
            yield

        async def close(self) -> None:
            return None

    good_chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, good_llm = make_agent(good_chunks)
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    previous = agent.last_usage

    agent._llm = FailingLLM()  # type: ignore[assignment]
    with pytest.raises(LLMError, match="401"):
        asyncio.run(agent.ask("again"))
    assert agent.last_usage is previous
    assert agent.message_usage == {1: previous}  # запись от неудавшегося хода не создана
    del good_llm


def test_apply_session_resets_usage() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    data = SessionData(settings=agent.settings, system_prompt="SP", name="test")
    agent.apply_session(data)
    assert agent.last_usage is None
    assert agent.message_usage == {}


# --- StatusBar ---


def test_fmt_tokens() -> None:
    assert fmt_tokens(0) == "0"
    assert fmt_tokens(456) == "456"
    assert fmt_tokens(2039) == "2039"
    assert fmt_tokens(9999) == "9999"
    assert fmt_tokens(12345) == "12.3k"
    assert fmt_tokens(1_500_000) == "1.5M"


# --- context_now: вес контекста прямо сейчас ---


def test_context_now_fresh_agent() -> None:
    agent, _ = make_agent([])
    # только system-промпт "SP" → оценка chars/4 = 8
    assert agent.context_now == (8, True)


def test_context_now_exact_after_turn() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 2039, "completion_tokens": 555}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    # точный вес сейчас = prompt + completion последнего хода
    assert agent.context_now == (2594, False)


def test_context_now_grows_during_streaming() -> None:
    chunks = [ChatChunk(content="x" * 40), ChatChunk(content="y" * 40)]
    agent, _ = make_agent(chunks, delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.02)  # до первого чанка
        base, _ = agent.context_now
        await asyncio.sleep(0.05)  # первый чанк уже доставлен
        grown, estimated = agent.context_now
        await task
        assert grown > base
        assert estimated is True

    asyncio.run(scenario())


def test_context_now_estimate_after_apply_session() -> None:
    agent, _ = make_agent([])
    data = SessionData(
        settings=agent.settings,
        system_prompt="SP",
        name="test",
        history=[
            Message(role=Role.USER, content="привет"),
            Message(role=Role.ASSISTANT, content="привет-привет"),
        ],
    )
    agent.apply_session(data)
    tokens, estimated = agent.context_now
    assert estimated is True
    assert tokens > 8  # system + загруженная история


# --- totals_part (StatusBar): накопительный расход сессии ---


def test_totals_part_zero_on_fresh_agent() -> None:
    agent, _ = make_agent([])
    assert totals_part(agent).plain == "in 0  out 0  Σ 0"


def test_totals_part_shows_accumulated() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("one"))
    asyncio.run(agent.ask("two"))
    assert totals_part(agent).plain == "in 200  out 100  Σ 300"


def test_totals_part_tilde_when_estimated() -> None:
    agent, _ = make_agent([ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    part = totals_part(agent)
    assert part.plain.startswith("in ~")
    assert f"out ~{len('Привет мир') // 4}" in part.plain
    assert "Σ ~" in part.plain


def test_status_bar_includes_totals() -> None:
    agent, _ = make_agent([])
    rendered = StatusBar(ChatTab(agent=agent)).render()
    assert "Σ 0" in rendered.plain


# --- context_part (StatusBar) ---


def test_context_part_on_fresh_agent() -> None:
    agent, _ = make_agent([])
    # первого ответа ещё нет — контекст неизвестен, индикатор скрыт
    assert context_part(agent) is None


def test_status_bar_hides_context_on_fresh_agent() -> None:
    agent, _ = make_agent([])
    rendered = StatusBar(ChatTab(agent=agent)).render()
    assert "context" not in rendered.plain


async def test_context_part_appears_with_first_response() -> None:
    chunks = [
        ChatChunk(content="часть "),
        ChatChunk(content="ответа"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 30, "completion_tokens": 8}}
        ),
    ]
    agent, _ = make_agent(chunks, delay=0.05)
    task = agent.start_ask("hi")
    await asyncio.sleep(0.02)  # «думаю»: первого ответа ещё нет — контекст скрыт
    assert agent.is_thinking is True
    assert context_part(agent) is None
    await asyncio.sleep(0.05)  # первый чанк доставлен — индикатор появился
    part = context_part(agent)
    assert part is not None
    assert part.plain.startswith("context ~")
    await task
    # точные числа от API: 30 + 8; окно 32768 (дефолт)
    assert context_part(agent).plain == "context 38/32.8k (0%)"


def test_context_part_exact_after_server_usage() -> None:
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 2039, "completion_tokens": 555}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert context_part(agent).plain == "context 2594/32.8k (8%)"


def test_context_now_excludes_reasoning_tokens() -> None:
    """Размышления в проекцию не уходят: context = prompt + видимый ответ."""
    chunks = [
        ChatChunk(reasoning="думаю…"),
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 555,
                    "completion_tokens_details": {"reasoning_tokens": 550},
                }
            }
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assert context_part(agent).plain == "context 105/32.8k (0%)"


def test_context_part_warns_when_truncated() -> None:
    long_text = "б" * 4000
    chunks = [
        ChatChunk(content="ok"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 2}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask(long_text))
    part = context_part(agent)
    assert part is not None
    assert part.plain.startswith("⚠ context")
    assert part.style == "yellow"


def test_context_part_estimated_mark() -> None:
    agent, _ = make_agent([ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    # оценка: system+user = 16, ответ "Привет мир" = 2 → 18
    assert context_part(agent).plain == "context ~18/32.8k (0%)"


# --- MessageList: tokens под репликами + лоадер ---


def test_tokens_line_under_assistant_message() -> None:
    chunks = [
        ChatChunk(content="Ответ"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    assert any(t.plain.strip() == "◈ tokens: in 100 · out 50" for t in texts)


def test_tokens_line_shows_think_separately() -> None:
    """Thinking-модели: think — отдельная составляющая строки (out здесь без размышлений)."""
    chunks = [
        ChatChunk(reasoning="думаю…"),
        ChatChunk(content="Ответ"),
        ChatChunk.from_sse_data(  # type: ignore[arg-type]
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "completion_tokens_details": {"reasoning_tokens": 45},
                }
            }
        ),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    assert any(t.plain.strip() == "◈ tokens: in 100 · out 5 · think 45" for t in texts)


def test_warning_note_rendered_yellow_bubble() -> None:
    """Заметки kind='warning' (переполнение контекста) — жёлтая полоса слева."""
    agent, _ = make_agent([ChatChunk(content="ok")])
    tab = ChatTab(agent=agent)
    tab.add_note("warning", "Контекст переполнен")
    rendered = MessageList(tab).render()
    assert isinstance(rendered, Group)
    panels = [b for b in rendered.renderables if isinstance(b, Panel)]
    assert any(
        "Контекст переполнен" in p.renderable.plain and p.border_style == "yellow"
        and p.title == "⚠ внимание"
        for p in panels
    )


def test_note_anchored_in_chat_timeline() -> None:
    """Заметка остаётся в таймлайне чата: новые реплики появляются после неё."""
    agent, _ = make_agent([ChatChunk(content="раз"), ChatChunk(content="два")])
    tab = ChatTab(agent=agent)
    asyncio.run(agent.ask("вопрос 1"))
    tab.add_note("system", "заметка в середине")
    asyncio.run(agent.ask("вопрос 2"))
    rendered = MessageList(tab).render()
    assert isinstance(rendered, Group)
    order: list[str] = []
    for block in rendered.renderables:
        if isinstance(block, Panel) and block.title is None:
            order.append("message")  # реплики — баблы без заголовка
        elif isinstance(block, Panel) and block.title == "ℹ инфо":
            order.append("note")
    assert order == ["message", "note", "message"]


def test_error_and_system_notes_rendered_as_bubbles() -> None:
    """error — красная полоса слева, system — оранжевая; заголовки с иконками."""
    agent, _ = make_agent([ChatChunk(content="ok")])
    tab = ChatTab(agent=agent)
    tab.add_note("error", "Ошибка LLM: таймаут")
    tab.add_note("system", "Команда /foo не найдена.")
    rendered = MessageList(tab).render()
    assert isinstance(rendered, Group)
    panels = [b for b in rendered.renderables if isinstance(b, Panel)]
    assert any(
        "Ошибка LLM" in p.renderable.plain and p.border_style == "red"
        and p.title == "✗ ошибка"
        for p in panels
    )
    assert any(
        "не найдена" in p.renderable.plain and p.border_style == "orange1"
        and p.title == "ℹ инфо"
        for p in panels
    )


def test_estimated_tokens_line_under_assistant_message() -> None:
    agent, _ = make_agent([ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    expected = (
        f"◈ tokens: in ~{agent.last_usage.prompt_tokens}"
        f" · out ~{len('Привет мир') // 4}"
    )
    assert any(t.plain.strip() == expected for t in texts)


def test_estimated_tokens_line_with_reasoning() -> None:
    """Оценочный ход thinking-модели: think виден и без точного usage."""
    reasoning = "раз" * 40
    agent, _ = make_agent([ChatChunk(reasoning=reasoning), ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    assert agent.last_usage is not None
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    expected = (
        f"◈ tokens: in ~{agent.last_usage.prompt_tokens}"
        f" · out ~{len('Привет мир') // 4} · think ~{len(reasoning) // 4}"
    )
    assert any(t.plain.strip() == expected for t in texts)


def test_streaming_tokens_line_shows_only_out() -> None:
    """Во время стрима in ещё неизвестен — строка печатает только out."""
    agent, _ = make_agent([ChatChunk(content="x" * 40), ChatChunk(content="y")], delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.07)  # первый чанк доставлен, стрим ещё идёт
        rendered = MessageList(ChatTab(agent=agent)).render()
        assert isinstance(rendered, Group)
        texts = [b for b in rendered.renderables if isinstance(b, Text)]
        expected = f"◈ tokens: out ~{len('x' * 40) // 4}"
        assert any(t.plain.strip() == expected for t in texts)
        await task

    asyncio.run(scenario())


def test_streaming_tokens_line_shows_live_think_estimate() -> None:
    """Во время стрима после размышлений: live-оценка out и think."""
    agent, _ = make_agent(
        [ChatChunk(reasoning="мысли"), ChatChunk(content="x" * 40), ChatChunk(content="y")],
        delay=0.05,
    )

    async def scenario() -> None:
        task = agent.start_ask("hi")
        # ждём доставки reasoning и первого чанка текста (стрим ещё идёт)
        while agent.is_streaming and (not agent.streaming_text or not agent.streaming_reasoning):
            await asyncio.sleep(0.01)
        assert agent.is_streaming
        rendered = MessageList(ChatTab(agent=agent)).render()
        assert isinstance(rendered, Group)
        texts = [b for b in rendered.renderables if isinstance(b, Text)]
        expected = f"◈ tokens: out ~{len('x' * 40) // 4} · think ~{len('мысли') // 4}"
        assert any(t.plain.strip() == expected for t in texts)
        await task

    asyncio.run(scenario())


def test_streaming_reasoning_block_has_no_tokens_line_yet() -> None:
    """Пока thinking-модель стримит только размышления — строки tokens ещё нет."""
    agent, _ = make_agent([ChatChunk(reasoning="мысли"), ChatChunk(content="x")], delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.07)  # reasoning доставлен, контента ещё нет
        rendered = MessageList(ChatTab(agent=agent)).render()
        assert isinstance(rendered, Group)
        texts = [b for b in rendered.renderables if isinstance(b, Text)]
        assert not any("tokens:" in t.plain for t in texts)
        await task

    asyncio.run(scenario())


def test_tokens_line_left_with_icon_and_separator() -> None:
    """Строка tokens слева с иконкой ◈ и отделена от реплики пустой строкой."""
    agent, _ = make_agent([ChatChunk(content="Привет мир")])
    asyncio.run(agent.ask("hi"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    items = rendered.renderables
    idx = next(
        i
        for i, b in enumerate(items)
        if isinstance(b, Text) and b.plain.startswith("  ◈ tokens:")
    )
    assert items[idx].style == TOKENS_STYLE
    assert isinstance(items[idx - 1], Text) and items[idx - 1].plain == ""


def test_thinking_loader_before_first_chunk() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")], delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.02)  # до первого чанка
        rendered = MessageList(ChatTab(agent=agent)).render()
        assert isinstance(rendered, Group)
        spinners = [b for b in rendered.renderables if isinstance(b, Spinner)]
        assert spinners, "лоадер «думаю…» должен быть виден до первого контента"
        assert "думаю" in spinners[0].text.plain
        await task

    asyncio.run(scenario())


def test_user_message_rendered_as_gray_bubble() -> None:
    """Реплика пользователя — бабл без обводки на тёмно-сером фоне (box=EMPTY)."""
    agent, _ = make_agent([ChatChunk(content="ok")])
    asyncio.run(agent.ask("привет"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    bubbles = [
        b
        for b in rendered.renderables
        if isinstance(b, Panel) and b.box == BUBBLE_BOX and b.title is None
    ]
    assert any(
        "привет" in b.renderable.plain and b.style == f"on {USER_BUBBLE_BG}" for b in bubbles
    )


def test_assistant_message_rendered_white_without_border() -> None:
    """Ответ ассистента — белый текст без обводки (Panel у реплики больше нет)."""
    agent, _ = make_agent([ChatChunk(content="ответ ассистента")])
    asyncio.run(agent.ask("hi"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    answers = [t for t in texts if t.plain == "ответ ассистента" and t.style == ANSWER_STYLE]
    assert answers
    assert not any(
        isinstance(b, Panel) and "ответ ассистента" in b.renderable.plain
        for b in rendered.renderables
    )


def test_reasoning_rendered_full_and_gray_after_ask() -> None:
    """После хода размышления видны целиком цитатой (серым) перед ответом — без обрезки «…»."""
    long_reasoning = "мысли " * 200  # раньше показывался только хвост 600 символов
    chunks = [
        ChatChunk(reasoning=long_reasoning),
        ChatChunk(content="итог"),
        ChatChunk(finish_reason="stop"),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    rendered = MessageList(ChatTab(agent=agent)).render()
    assert isinstance(rendered, Group)
    quotes = [
        b for b in rendered.renderables if isinstance(b, Panel) and b.box == REASONING_BOX
    ]
    assert any(
        isinstance(q.renderable, Text)
        and q.renderable.style == REASONING_STYLE
        and long_reasoning in q.renderable.plain
        and "…" not in q.renderable.plain[:1]
        for q in quotes
    )
    texts = [b for b in rendered.renderables if isinstance(b, Text)]
    assert any(t.plain == "итог" and t.style == ANSWER_STYLE for t in texts)


def test_streaming_reasoning_shown_in_full() -> None:
    """Во время стрима размышления показываются полностью, цитатой, с курсором."""
    long_reasoning = "мысли " * 200
    agent, _ = make_agent(
        [ChatChunk(reasoning=long_reasoning), ChatChunk(content="итог")], delay=0.05
    )

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.07)  # reasoning доставлен, контент ещё нет
        rendered = MessageList(ChatTab(agent=agent)).render()
        assert isinstance(rendered, Group)
        quotes = [
            b for b in rendered.renderables if isinstance(b, Panel) and b.box == REASONING_BOX
        ]
        assert any(
            isinstance(q.renderable, Text)
            and q.renderable.plain == long_reasoning + "▌"
            and q.renderable.style == REASONING_STYLE
            for q in quotes
        )
        await task

    asyncio.run(scenario())

