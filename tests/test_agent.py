"""Agent.ask() с мок-клиентом LLM."""

import asyncio

import pytest

from agent.config.schema import AgentSettings, validate_config
from agent.core.agent import CANCELLED_MARK, Agent, AgentBusyError
from agent.core.message import ChatChunk, Role, ToolCallDelta
from agent.core.task import TaskPhase
from agent.llm.client import LLMError


class MockLLM:
    """Мок LLMClient: отдаёт заданные чанки, запоминает параметры запроса."""

    def __init__(self, chunks: list[ChatChunk], delay: float = 0.0) -> None:
        self.chunks = chunks
        self.delay = delay
        self.calls: list[tuple[object, str, str]] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append((request, api_base, api_key))
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
                "p2": {"api_base": "http://p2/v1", "api_key": "", "models": ["m2"]},
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


def test_ask_streams_and_stores_history() -> None:
    chunks = [
        ChatChunk(content="Привет"),
        ChatChunk(content=" мир"),
        ChatChunk(finish_reason="stop"),
    ]
    agent, _ = make_agent(chunks)
    result = asyncio.run(agent.ask("hi"))
    assert result == "Привет мир"
    history = agent.memory.history
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT]
    assert history[1].content == "Привет мир"
    assert agent.is_streaming is False


def test_ask_resolves_provider_per_request() -> None:
    agent, llm = make_agent([ChatChunk(content="ok")])
    agent.set_model("p2:m2")
    asyncio.run(agent.ask("hi"))
    request, api_base, api_key = llm.calls[0]
    assert api_base == "http://p2/v1"
    assert api_key == ""
    assert request.model == "m2"  # без префикса провайдера


def test_ask_passes_generation_params() -> None:
    agent, llm = make_agent([ChatChunk(content="ok")])
    agent.settings.temperature = 0.2
    agent.settings.top_p = 0.9
    agent.settings.max_tokens = 100
    agent.settings.stop = ["\n", "END"]
    asyncio.run(agent.ask("hi"))
    request = llm.calls[0][0]
    assert request.temperature == 0.2
    assert request.top_p == 0.9
    assert request.max_tokens == 100
    assert request.stop == ["\n", "END"]
    # системный промпт в начале сообщений
    assert request.messages[0].role == Role.SYSTEM
    assert request.messages[0].content == "SP"


def test_ask_accumulates_tool_calls() -> None:
    chunks = [
        ChatChunk(
            tool_call_deltas=[
                ToolCallDelta(index=0, id="c1", function_name="get", function_arguments='{"a":')
            ]
        ),
        ChatChunk(tool_call_deltas=[ToolCallDelta(index=0, function_arguments="1}")]),
        ChatChunk(finish_reason="tool_calls"),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("use tool"))
    assistant = agent.memory.history[-1]
    assert assistant.tool_calls is not None
    tc = assistant.tool_calls[0]
    assert tc.id == "c1"
    assert tc.function.name == "get"
    assert tc.function.arguments == '{"a":1}'


def test_llm_error_propagates_and_chat_state_clean() -> None:
    class FailingLLM:
        async def astream(self, request: object, api_base: str, api_key: str):
            raise LLMError("HTTP 401: invalid api key", status=401)
            yield

        async def close(self) -> None:
            return None

    llm = FailingLLM()  # type: ignore[assignment]
    agent = Agent(
        name="t",
        settings=AgentSettings.from_config(make_config()),
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
    )
    with pytest.raises(LLMError, match="401"):
        asyncio.run(agent.ask("hi"))
    # user-сообщение осталось, assistant не добавлен, агент свободен
    assert [m.role for m in agent.memory.history] == [Role.USER]
    assert agent.is_streaming is False


def test_start_ask_busy() -> None:
    agent, _ = make_agent([ChatChunk(content="ok")], delay=0.2)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        with pytest.raises(AgentBusyError):
            agent.start_ask("again")
        assert task.cancelled() is False
        await task

    asyncio.run(scenario())


def test_cancel_ask_saves_partial() -> None:
    agent, _ = make_agent([ChatChunk(content="partial"), ChatChunk(content="")], delay=0.05)

    async def scenario() -> None:
        task = agent.start_ask("hi")
        await asyncio.sleep(0.1)  # первый чанк (0.05) уже доставлен
        assert agent.cancel_ask() is True
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    contents = [m.content for m in agent.memory.history]
    assert "partial" in contents[1]
    assert CANCELLED_MARK in contents[1]


def test_ask_reasoning_only_length_raises_without_empty_message() -> None:
    """Thinking-модель израсходовала max_tokens размышлениями (delta.reasoning):
    LLMError, пустой assistant в историю не попадает."""
    chunks = [
        ChatChunk(reasoning="думаю над библиографией…"),
        ChatChunk(finish_reason="length"),
    ]
    agent, _ = make_agent(chunks)
    with pytest.raises(LLMError, match="max_tokens"):
        asyncio.run(agent.ask("hi"))
    assert [m.role for m in agent.memory.history] == [Role.USER]
    assert agent.is_streaming is False
    assert agent.streaming_reasoning == ""  # сброс в finally


def test_ask_persists_reasoning_on_assistant_message() -> None:
    """Размышления thinking-модели сохраняются в истории (для UI) и уходят в API-проекцию
    только через content — поле reasoning не сериализуется в to_api()."""
    chunks = [
        ChatChunk(reasoning="шаг 1"),
        ChatChunk(reasoning="шаг 2"),
        ChatChunk(content="ответ"),
        ChatChunk(finish_reason="stop"),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("hi"))
    assistant = agent.memory.history[-1]
    assert assistant.role is Role.ASSISTANT
    assert assistant.content == "ответ"
    assert assistant.reasoning == "шаг 1шаг 2"
    # reasoning не уходит в API
    assert "reasoning" not in assistant.to_api()
    # ...и в проекцию для LLM попадает только content
    projection = agent.context_builder.build_messages(
        agent.system_prompt, agent.memory.tail, summary=None
    )
    assert all("reasoning" not in m.to_api() for m in projection)


def test_ask_empty_answer_without_length_raises() -> None:
    """Пустой ответ без контента и tool_calls — LLMError, история не замусоривается."""
    chunks = [ChatChunk(finish_reason="stop")]
    agent, _ = make_agent(chunks)
    with pytest.raises(LLMError, match="пустой ответ"):
        asyncio.run(agent.ask("hi"))
    assert [m.role for m in agent.memory.history] == [Role.USER]


def test_ask_runs_task_start_tool_and_updates_state() -> None:
    """Инструмент task_start запускает задачу и переводит автомат в планирование."""
    chunks = [
        ChatChunk(
            tool_call_deltas=[
                ToolCallDelta(
                    index=0,
                    id="c1",
                    function_name="task_start",
                    function_arguments='{"description": "написать модуль", "steps": ["a", "b"]}',
                )
            ]
        ),
        ChatChunk(finish_reason="tool_calls"),
    ]
    agent, _ = make_agent(chunks)
    asyncio.run(agent.ask("начни задачу"))
    state = agent.memory.task.state
    assert state.phase is TaskPhase.PLANNING
    assert state.description == "написать модуль"
    assert state.steps == ["a", "b"]


def test_ask_projects_task_state_into_context() -> None:
    """Активная задача попадает в контекст LLM на каждом ходу."""
    agent, llm = make_agent([ChatChunk(content="ok")])
    agent.memory.task.start("написать модуль", ["подготовить", "реализовать"])
    asyncio.run(agent.ask("hi"))
    request = llm.calls[0][0]
    contents = [m.content or "" for m in request.messages]
    assert any("Активная задача" in c for c in contents)
    assert any("Этап: планирование" in c for c in contents)
    assert any("Шаг: 1 из 2" in c for c in contents)
