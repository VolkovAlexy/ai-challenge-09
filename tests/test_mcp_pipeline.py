"""Пайплайн MCP-инструментов: цепочка `review_changes` -> `summarize` -> `saveToFile`.

Проверяет две вещи:
1. `Pipeline` автоматически исполняет цепочку инструментов, пробрасывая вывод
   предыдущего шага в следующий (плейсхолдер `${prev}`), и останавливается на ошибке.
2. MCP-сервер `mcp_pipeline` реально отдаёт инструменты, и сквозной прогон цепочки
   через `run_pipeline` (через `McpAdapter`) собирает изменения в git-репозитории
   за последние сутки, сжимает их и сохраняет отчёт в project_log.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from agent.config.schema import McpServer
from agent.tools.context import ToolContext
from agent.tools.mcp import McpAdapter
from agent.tools.registry import ToolRegistry, ToolResult
from mcp_pipeline.pipeline import Pipeline, PipelineStep


class FakeTool:
    """Минимальный инструмент: записывает порядок вызовов, может вернуть ошибку."""

    def __init__(self, name: str, calls: list[str], output: str = "ok") -> None:
        self.name = name
        self.description = name
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self._calls = calls
        self._output = output
        self.last_args: dict[str, Any] = {}

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self._calls.append(self.name)
        self.last_args = arguments
        return ToolResult(output=self._output)


def _ctx() -> ToolContext:
    # Инструменты цепочки не объявляют owner_* параметров, поэтому ctx используется
    # только как передаваемый объект; поля сессии/агента не задействуются.
    return ToolContext(session=None, agent=None, project_id="p", run_id="r")  # type: ignore[arg-type]


async def test_pipeline_runs_chain_in_order_and_threads_data() -> None:
    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(FakeTool("review_changes", calls, "отчёт: LLM-агент"))
    registry.register(FakeTool("summarize", calls, "сводка: LLM-агент"))
    registry.register(FakeTool("saveToFile", calls, "сохранено"))

    pipeline = Pipeline(registry)
    results = await pipeline.run(
        [
            PipelineStep(tool="review_changes", params={"days": 1}),
            PipelineStep(tool="summarize", params={"text": "${prev}"}),
            PipelineStep(tool="saveToFile", params={"content": "${prev}"}),
        ],
        _ctx(),
    )

    assert [r.tool for r in results] == ["review_changes", "summarize", "saveToFile"]
    assert calls == ["review_changes", "summarize", "saveToFile"]  # автоматический порядок
    assert results[0].output == "отчёт: LLM-агент"
    # данные проброшены между шагами: summarize увидел вывод review_changes,
    # saveToFile — вывод summarize
    summarize = registry.get("summarize")
    save = registry.get("saveToFile")
    assert isinstance(summarize, FakeTool) and summarize.last_args["text"] == "отчёт: LLM-агент"
    assert isinstance(save, FakeTool) and save.last_args["content"] == "сводка: LLM-агент"


async def test_pipeline_stops_on_error_step() -> None:
    registry = ToolRegistry()

    class ErrTool(FakeTool):
        async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
            self._calls.append(self.name)
            return ToolResult(output="boom", is_error=True)

    calls: list[str] = []
    registry.register(ErrTool("review_changes", calls))
    registry.register(FakeTool("summarize", calls))

    pipeline = Pipeline(registry)
    results = await pipeline.run(
        [
            PipelineStep(tool="review_changes", params={}),
            PipelineStep(tool="summarize", params={"text": "${prev}"}),
        ],
        _ctx(),
    )

    assert results[0].is_error is True
    assert len(results) == 1  # цепочка остановилась
    assert calls == ["review_changes"]


async def test_pipeline_stops_on_unknown_tool() -> None:
    registry = ToolRegistry()
    pipeline = Pipeline(registry)
    results = await pipeline.run([PipelineStep(tool="ghost", params={})], _ctx())
    assert results[0].is_error is True
    assert "ghost" in results[0].output


def _spec() -> McpServer:
    return McpServer(
        transport="stdio",
        command=sys.executable,
        args=["-m", "mcp_pipeline.server", "--no-llm"],
    )


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)


async def test_mcp_server_exposes_tools() -> None:
    adapter = McpAdapter("pipeline")
    await adapter.connect(_spec())
    try:
        tools = await adapter.list_tools()
        names = {t["name"] for t in tools}
        assert names == {"review_changes", "summarize", "saveToFile", "run_pipeline"}
    finally:
        await adapter.close()


async def test_pipeline_end_to_end_via_mcp(tmp_path: Path) -> None:
    """Сквозной прогон: review_changes -> summarize -> saveToFile через настоящий MCP-транспорт."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "note.md").write_text("LLM-агент собирает инструменты в цепочку.", encoding="utf-8")
    subprocess.run(["git", "add", "note.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add note about LLM-агент"], cwd=repo, check=True)

    logdir = tmp_path / "project_log"
    logdir.mkdir()

    adapter = McpAdapter("pipeline")
    await adapter.connect(_spec())
    try:
        assert await adapter.sync_tools(ToolRegistry()) == 4
        result = await adapter.call_tool(
            "run_pipeline",
            {
                "steps": [
                    {"tool": "review_changes", "params": {"days": 1, "repo": str(repo)}},
                    {"tool": "summarize", "params": {"text": "${prev}", "max_sentences": 2}},
                    {
                        "tool": "saveToFile",
                        "params": {"content": "${prev}", "root": str(logdir)},
                    },
                ]
            },
        )

        assert not result.is_error, result.output
        # каждый шаг цепочки отработал и данные проброшены (${prev})
        assert "[review_changes] review_changes: Изменения за последние" in result.output
        assert "[summarize] summarize: Изменения за последние" in result.output
        assert "[saveToFile] saveToFile: Сохранено" in result.output

        saved_files = list(logdir.glob("*.md"))
        assert len(saved_files) == 1
        # на диск легла выжимка (summarize) отчёта ревью в файл <дата_время>.md
        saved = saved_files[0].read_text(encoding="utf-8")
        assert saved_files[0].stem.startswith("20")  # имя из даты/времени создания
        assert "Изменения за последние" in saved
        assert "LLM-агент" in saved
    finally:
        await adapter.close()


async def test_save_to_file_creates_timestamped_md(tmp_path: Path) -> None:
    adapter = McpAdapter("pipeline")
    await adapter.connect(_spec())
    try:
        result = await adapter.call_tool(
            "saveToFile",
            {"content": "сводка изменений", "root": str(tmp_path)},
        )
        assert not result.is_error, result.output
        assert "Сохранено" in result.output
        saved_files = list(tmp_path.glob("*.md"))
        assert len(saved_files) == 1
        assert saved_files[0].stem.startswith("20")  # имя = дата/время создания
        assert saved_files[0].read_text(encoding="utf-8") == "сводка изменений"
    finally:
        await adapter.close()
