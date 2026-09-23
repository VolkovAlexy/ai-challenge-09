"""ToolRegistry: приоритет «статический > динамический» и проброс ToolContext."""

import asyncio
from typing import Any

from agent.config.schema import AgentSettings
from agent.core.agent import Agent
from agent.core.message import ChatChunk, Role, ToolCallDelta
from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry, ToolResult
from tests.test_agent import make_config


class FakeTool:
    """Минимальная реализация Tool для тестов реестра."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = name
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(output="ok")


class CaptureTool:
    """Инструмент, запоминающий переданный ToolContext (для проброса через ask)."""

    name = "capture"
    description = "запоминает ctx"

    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.ctx: ToolContext | None = None

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.ctx = ctx
        return ToolResult(output="captured")


class RoundLLM:
    """Мок LLM: отдаёт отдельную последовательность чанков на каждый вызов."""

    def __init__(self, rounds: list[list[ChatChunk]]) -> None:
        self.rounds = [list(r) for r in rounds]
        self.calls: list[tuple[object, str, str]] = []

    async def astream(self, request: object, api_base: str, api_key: str):
        self.calls.append((request, api_base, api_key))
        seq = self.rounds.pop(0) if self.rounds else [ChatChunk(content="")]
        for chunk in seq:
            yield chunk

    async def close(self) -> None:
        return None


def test_static_tool_shadows_dynamic_with_same_name() -> None:
    reg = ToolRegistry()
    static = FakeTool("foo")
    dynamic = FakeTool("foo")
    reg.register(static)
    reg.register_dynamic(dynamic)
    assert reg.get("foo") is static
    assert len(reg) == 1


def test_dynamic_registered_when_no_static() -> None:
    reg = ToolRegistry()
    dynamic = FakeTool("bar")
    reg.register_dynamic(dynamic)
    assert reg.get("bar") is dynamic
    assert len(reg) == 1


def test_unregister_removes_from_both_pools() -> None:
    reg = ToolRegistry()
    reg.register(FakeTool("x"))
    reg.register_dynamic(FakeTool("y"))
    reg.unregister("x")
    reg.unregister("y")
    assert reg.get("x") is None
    assert reg.get("y") is None
    assert len(reg) == 0


def test_all_and_api_tools_static_first() -> None:
    reg = ToolRegistry()
    reg.register_dynamic(FakeTool("dyn"))
    reg.register(FakeTool("static"))
    names = [t.name for t in reg.all()]
    assert names == ["static", "dyn"]
    api = reg.to_api_tools()
    assert [t["function"]["name"] for t in api] == ["static", "dyn"]


def test_execute_tool_call_passes_tool_context() -> None:
    llm = RoundLLM(
        [
            [
                ChatChunk(
                    tool_call_deltas=[
                        ToolCallDelta(
                            index=0,
                            id="c1",
                            function_name="capture",
                            function_arguments="{}",
                        )
                    ]
                ),
                ChatChunk(finish_reason="tool_calls"),
            ],
            [ChatChunk(content="done"), ChatChunk(finish_reason="stop")],
        ]
    )
    agent = Agent(
        name="test",
        settings=AgentSettings.from_config(make_config()),
        system_prompt="SP",
        llm=llm,  # type: ignore[arg-type]
        config=make_config(),  # type: ignore[arg-type]
        project_id="proj",
    )
    capture = CaptureTool()
    agent.register_tools([capture])
    result = asyncio.run(agent.ask("hi"))
    assert result == "done"
    assert capture.ctx is not None
    assert capture.ctx.session is agent.memory
    assert capture.ctx.agent is agent
    assert capture.ctx.project_id == "proj"
    assert capture.ctx.run_id
    tool_msgs = [m for m in agent.memory.history if m.role is Role.TOOL]
    assert tool_msgs[0].content == "captured"
