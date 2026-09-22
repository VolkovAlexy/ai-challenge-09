"""Инструменты делегирования: delegate_/list_subagents с мок-клиентами LLM."""

import asyncio
from pathlib import Path

from agent.config.schema import AgentSettings
from agent.core.agent import Agent
from agent.core.message import ChatChunk, Role, ToolCallDelta
from agent.llm.client import LLMError
from agent.memory.persistence import SessionStore
from agent.tools.delegate import DelegateTool, ListSubagentsTool, delegate_tools
from tests.test_agent import make_config


class SequencerLLM:
    """Мок LLM: отдаёт отдельную последовательность чанков на каждый вызов."""

    def __init__(self, sequences: list[list[ChatChunk]]) -> None:
        self.sequences = [list(seq) for seq in sequences]
        self.calls: list[tuple[object, str, str]] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append((request, api_base, api_key))
        sequence = self.sequences.pop(0) if self.sequences else [ChatChunk(content="")]
        for chunk in sequence:
            yield chunk

    async def close(self) -> None:
        return None


class FixedLLM:
    """Мок LLM: всегда отдаёт один и тот же ответ."""

    def __init__(self, content: str = "A") -> None:
        self.content = content
        self.calls: list[tuple[object, str, str]] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append((request, api_base, api_key))
        yield ChatChunk(content=self.content)
        yield ChatChunk(finish_reason="stop")

    async def close(self) -> None:
        return None


class FailingLLM:
    """Мок LLM: сразу кидает LLMError."""

    async def astream(self, request: object, api_base: str, api_key: str):
        raise LLMError("boom", status=500)
        yield

    async def close(self) -> None:
        return None


def _local_env() -> tuple[SessionStore, object, AgentSettings]:
    store = SessionStore(Path(":memory:"))
    store.ensure_project("proj", "P")
    config = make_config()
    return store, config, AgentSettings.from_config(config)


def _add_profile(
    store: SessionStore, name: str, content: str, project_id: str = "proj"
) -> object:
    profile = store.create_profile(name, content)
    existing = store.list_project_profiles(project_id)
    store.set_project_profiles(project_id, [*existing, profile.id])
    return profile


def _orchestrator(
    llm: object, store: SessionStore, config: object, settings: AgentSettings
) -> Agent:
    agent = Agent(
        name="орк",
        settings=settings,
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
        longterm=None,
        project_id="proj",
    )
    agent.register_tools(delegate_tools(agent, store, llm, config))  # type: ignore[arg-type]
    return agent


def _delegate(agent: Agent) -> DelegateTool:
    tool = agent._tools.get("delegate")
    assert isinstance(tool, DelegateTool)
    return tool


def _list_subagents(agent: Agent) -> ListSubagentsTool:
    tool = agent._tools.get("list_subagents")
    assert isinstance(tool, ListSubagentsTool)
    return tool


def _in_execution(agent: Agent) -> None:
    agent.memory.task.start("задача", ["шаг"])
    agent.memory.task.set_plan_confirmed(True)
    agent.memory.task.to_execution()


def test_delegate_executes_subagent_and_returns_answer() -> None:
    store, config, settings = _local_env()
    llm = SequencerLLM(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        ToolCallDelta(
                            index=0,
                            id="c1",
                            function_name="delegate",
                            function_arguments=(
                                '{"role":"писатель","task":"напиши главу","context":"вот данные"}'
                            ),
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="Глава написана"), ChatChunk(finish_reason="stop")],
            [ChatChunk(content="Учту."), ChatChunk(finish_reason="stop")],
        ]
    )
    _add_profile(store, "писатель", "Ты писатель")
    agent = _orchestrator(llm, store, config, settings)
    _in_execution(agent)
    result = asyncio.run(agent.ask("напиши главу"))
    assert result == "Учту."
    assert len(llm.calls) == 3
    sub_request = llm.calls[1][0]
    messages = sub_request.messages
    assert messages[0].role is Role.SYSTEM
    assert messages[0].content == "Ты писатель"
    tool_msgs = [m for m in agent.memory.history if m.role is Role.TOOL]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "Глава написана"
    kinds = [e.kind for e in agent.subagent_events]
    assert kinds == ["started", "delta", "done"]
    assert agent.subagent_events[1].content == "Глава написана"
    assert agent.subagent_events[2].profile == "писатель"
    # субагент — чистый исполнитель: у него нет инструментов конечного автомата задачи
    tool_names = [t["function"]["name"] for t in (sub_request.tools or [])]
    assert not any(name.startswith("task_") for name in tool_names)


def test_delegate_resolves_profile_by_name_and_id() -> None:
    store, config, settings = _local_env()
    llm = FixedLLM()
    profile = _add_profile(store, "писатель", "Ты писатель")
    agent = _orchestrator(llm, store, config, settings)
    _in_execution(agent)
    tool = _delegate(agent)
    by_name = asyncio.run(tool.execute({"role": "писатель", "task": "T"}))
    assert by_name.is_error is False
    assert by_name.output == "A"
    by_id = asyncio.run(tool.execute({"role": profile.id, "task": "T"}))
    assert by_id.is_error is False
    unknown = asyncio.run(tool.execute({"role": "нет", "task": "T"}))
    assert unknown.is_error is True
    assert "не найден" in unknown.output


def test_delegate_requires_role_and_task() -> None:
    store, config, settings = _local_env()
    _add_profile(store, "писатель", "Ты писатель")
    llm = FixedLLM()
    agent = _orchestrator(llm, store, config, settings)
    tool = _delegate(agent)
    no_role = asyncio.run(tool.execute({"task": "T"}))
    assert no_role.is_error is True
    no_task = asyncio.run(tool.execute({"role": "писатель"}))
    assert no_task.is_error is True


def test_delegate_invalid_model_is_error() -> None:
    store, config, settings = _local_env()
    _add_profile(store, "писатель", "Ты писатель")
    llm = FixedLLM()
    agent = _orchestrator(llm, store, config, settings)
    _in_execution(agent)
    tool = _delegate(agent)
    result = asyncio.run(tool.execute({"role": "писатель", "task": "T", "model": "p1:nope"}))
    assert result.is_error is True


def test_delegate_propagates_subagent_llm_error() -> None:
    store, config, settings = _local_env()
    _add_profile(store, "писатель", "Ты писатель")
    llm = FailingLLM()  # type: ignore[assignment]
    agent = _orchestrator(llm, store, config, settings)
    _in_execution(agent)
    tool = _delegate(agent)
    result = asyncio.run(tool.execute({"role": "писатель", "task": "T"}))
    assert result.is_error is True
    assert "Ошибка субагента" in result.output


def test_list_subagents_returns_project_profiles() -> None:
    store, config, settings = _local_env()
    _add_profile(store, "писатель", "Ты писатель")
    _add_profile(store, "редактор", "Ты редактор")
    llm = FixedLLM()
    agent = _orchestrator(llm, store, config, settings)
    tool = _list_subagents(agent)
    result = asyncio.run(tool.execute({}))
    assert "писатель" in result.output
    assert "редактор" in result.output


def test_delegate_refused_outside_execution_or_validation() -> None:
    store, config, settings = _local_env()
    _add_profile(store, "писатель", "Ты писатель")
    llm = FixedLLM()
    agent = _orchestrator(llm, store, config, settings)
    tool = _delegate(agent)
    # в состоянии без задачи (IDLE) делегировать нельзя
    result = asyncio.run(tool.execute({"role": "писатель", "task": "T"}))
    assert result.is_error is True
    assert "доступно только" in result.output
    # в планировании (до подтверждения плана) тоже нельзя
    agent.memory.task.start("задача", ["шаг"])
    result = asyncio.run(tool.execute({"role": "писатель", "task": "T"}))
    assert result.is_error is True
    assert "доступно только" in result.output
