"""Sticky Facts: парсинг JSON, FactsExtractor и хук в Agent.ask()."""

import asyncio

import pytest

from my_agent.config.schema import AgentSettings
from my_agent.core.agent import Agent
from my_agent.core.context import FACTS_HEADER
from my_agent.core.message import ChatChunk, Message, Role, Usage
from my_agent.llm.client import LLMError
from my_agent.memory.facts import FactsExtractor, parse_facts


def make_config() -> object:
    from my_agent.config.schema import validate_config

    return validate_config(
        {
            "providers": {
                "p1": {"api_base": "http://p1/v1", "api_key": "k1", "models": ["m1"]}
            },
            "default_model": "p1:m1",
        }
    )


# --- parse_facts ---


def test_parse_facts_plain_json_exact() -> None:
    assert parse_facts('{"a": "1", "b": "2"}') == {"a": "1", "b": "2"}


def test_parse_facts_strips_markdown_fence() -> None:
    raw = "```json\n{\"goal\": \"тест\"}\n```"
    assert parse_facts(raw) == {"goal": "тест"}


def test_parse_facts_json_inside_text() -> None:
    raw = "Вот обновлённые факты: {\"goal\": \"тест\"} — готово."
    assert parse_facts(raw) == {"goal": "тест"}


def test_parse_facts_rejects_garbage() -> None:
    assert parse_facts("не JSON вообще") is None
    assert parse_facts('{"broken": ') is None
    assert parse_facts("[1, 2, 3]") is None  # не объект
    assert parse_facts("```") is None


def test_parse_facts_drops_empty_keys_and_values() -> None:
    assert parse_facts('{"a": "", "": "x", "b": "ok"}') == {"b": "ok"}


# --- FactsExtractor ---


class FactsLLM:
    """Фейковый LLMClient: отдаёт заготовленный текст + usage, копит запросы."""

    def __init__(self, text: str, usage: Usage | None = None) -> None:
        self.text = text
        self.usage = usage
        self.requests: list[object] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.requests.append(request)
        yield ChatChunk(content=self.text)
        if self.usage is not None:
            yield ChatChunk(usage=self.usage)

    async def close(self) -> None:
        return None


def make_extractor(text: str, usage: Usage | None = None) -> tuple[FactsExtractor, FactsLLM]:
    llm = FactsLLM(text, usage=usage)
    return FactsExtractor(llm), llm  # type: ignore[arg-type]


def test_extractor_updates_facts() -> None:
    extractor, llm = make_extractor('{"goal": "задача", "deadline": "пт"}')
    result = asyncio.run(
        extractor.update(
            model="m1",
            api_base="http://p1/v1",
            api_key="k",
            facts={"goal": "старое"},
            history=[Message(role=Role.USER, content="новое сообщение")],
        )
    )
    assert result.facts == {"goal": "задача", "deadline": "пт"}
    request = llm.requests[0]
    assert request.model == "m1"
    assert request.temperature == 0.0
    # текущие факты и хвост диалога в теле запроса
    body = request.messages[-1].content
    assert '"goal": "старое"' in body
    assert "новое сообщение" in body


def test_extractor_uses_server_usage() -> None:
    extractor, _ = make_extractor(
        '{"goal": "x"}', usage=Usage(prompt_tokens=100, completion_tokens=10)
    )
    result = asyncio.run(
        extractor.update(
            model="m1", api_base="b", api_key="k", facts={}, history=[]
        )
    )
    assert (result.in_tokens, result.out_tokens) == (100, 10)
    assert result.estimated is False


def test_extractor_estimates_without_usage() -> None:
    extractor, _ = make_extractor('{"goal": "x"}')
    result = asyncio.run(
        extractor.update(model="m1", api_base="b", api_key="k", facts={}, history=[])
    )
    assert result.in_tokens > 0 and result.out_tokens > 0
    assert result.estimated is True


def test_extractor_raises_on_non_json() -> None:
    extractor, _ = make_extractor("Просто текст без JSON.")
    with pytest.raises(LLMError, match="не-JSON"):
        asyncio.run(
            extractor.update(model="m1", api_base="b", api_key="k", facts={}, history=[])
        )


# --- хук в Agent.ask() ---


class TwoCallLLM:
    """Фейковый LLMClient: первый вызов — facts, второй — ответ чата."""

    def __init__(self, facts_text: str, answer: str) -> None:
        self.facts_text = facts_text
        self.answer = answer
        self.calls: list[object] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append(request)
        text = self.facts_text if len(self.calls) == 1 else self.answer
        yield ChatChunk(content=text)
        yield ChatChunk(finish_reason="stop")

    async def close(self) -> None:
        return None


def make_facts_agent(
    facts_text: str, answer: str = "Ответ"
) -> tuple[Agent, TwoCallLLM]:
    llm = TwoCallLLM(facts_text, answer)
    settings = AgentSettings.from_config(make_config())  # type: ignore[arg-type]
    settings.context_strategy = "facts"
    agent = Agent(
        name="test",
        settings=settings,
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    return agent, llm


def test_ask_in_facts_mode_updates_and_projects_facts() -> None:
    agent, llm = make_facts_agent('{"goal": "написать тест"}', "Ответ")
    asyncio.run(agent.ask("делаем задачу"))
    # два вызова: сначала извлечение facts, затем чат
    assert len(llm.calls) == 2
    assert agent.memory.facts == {"goal": "написать тест"}
    chat_request = llm.calls[1]
    roles = [m.role for m in chat_request.messages]
    assert roles == [Role.SYSTEM, Role.SYSTEM, Role.USER]
    assert FACTS_HEADER in chat_request.messages[1].content
    assert "goal" in chat_request.messages[1].content
    # расход вызова facts попал в totals
    assert agent.totals.in_tokens > 0


def test_ask_keeps_old_facts_on_bad_json() -> None:
    agent, llm = make_facts_agent("не JSON")
    notes: list[str] = []
    agent.on_facts = notes.append
    asyncio.run(agent.ask("привет"))
    assert agent.memory.facts == {}
    assert len(llm.calls) == 2  # чат всё равно состоялся
    assert notes and "facts" in notes[0].lower()


def test_facts_extracted_only_in_facts_mode() -> None:
    llm = TwoCallLLM('{"goal": "x"}', "Ответ")
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),  # type: ignore[arg-type]
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    asyncio.run(agent.ask("привет"))
    assert len(llm.calls) == 1  # только чат, без вызова извлечения
    assert agent.memory.facts == {}
