"""MCP-сервер `pipeline`: ревью изменений кода и их сжатие.

Инструменты цепочки:
- `review_changes` — собирает изменения в git-репозитории за последние сутки
  (коммиты + незакоммиченные правки) и возвращает текстовый отчёт.
- `summarize` — сжимает отчёт в краткую выжимку (через LLM, если передан
  `config`/`llm`; иначе — extractive-срез по первым предложениям).
- `saveToFile` — сохраняет результат в каталог `project_log` в файл
  `<дата_время>.md` (имя генерируется из времени создания файла).
- `run_pipeline` — мета-инструмент: один вызов исполняет всю цепочку
  `review_changes -> summarize -> saveToFile` через `Pipeline` (автоматическая
  передача вывода между шагами по `${prev}`).

Запуск:
    uv run agent-mcp-pipeline --stdio          # stdio (спавнится McpAdapter)
    uv run agent-mcp-pipeline --http --port 8897  # streamable-http

Для LLM-сводки сервер читает `config.json` (`--config`); без него (или с
`--no-llm`) сводка считается правиловым extractive-срезом — так удобно в тестах.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.applications import Starlette

from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry, ToolResult
from mcp_pipeline.pipeline import Pipeline, PipelineStep

DEFAULT_HTTP_PORT = 8897
DEFAULT_CONFIG = "config.json"
# Каталог, куда по умолчанию сохраняются результаты цепочки.
PROJECT_LOG = "project_log"
# Максимальная длина диффа в отчёте ревью (чтобы LLM-сводка не раздувалась).
MAX_DIFF_CHARS = 6000

_SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*\s*")


def _split_sentences(text: str) -> list[str]:
    """Наивный, но детерминированный сплит на предложения (учитывает переводы строк)."""
    parts = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text)]
    return [p for p in parts if p]


def make_server(config: Any = None, llm: Any = None) -> MCPServer:
    """Создаёт MCP-сервер `pipeline` с инструментами ревью/сжатия/сохранения.

    `config`/`llm` необязательны: их наличие включает LLM-сводку, иначе
    `summarize` сосредотачивается на extractive-срезе (передается в тестах).
    """

    async def _git(base: Path, *args: str) -> tuple[int, str, str]:
        """Выполняет `git -C <base> <args>`; возвращает (код, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(base),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def _review(days: int = 1, repo: str = ".") -> str:
        """Собирает изменения за последние `days` суток в git-репозитории `repo`."""
        base = Path(repo).expanduser().resolve()
        code, _out, err = await _git(base, "rev-parse", "--is-inside-work-tree")
        if code != 0:
            return f"Ошибка: «{repo}» не является git-репозиторием: {err.strip()}"
        since = f"{days} day ago"
        lines: list[str] = [f"Изменения за последние {days} суток в «{base}»."]

        _c_code, c_out, _ = await _git(
            base, "log", f"--since={since}", "--date=iso", "--pretty=format:%h|%ad|%an|%s"
        )
        commits = [c for c in c_out.splitlines() if c.strip()]
        if commits:
            lines.append(f"\nКоммиты ({len(commits)}):")
            lines.extend(f"- {c}" for c in commits)
        else:
            lines.append("\nКоммитов за этот период не найдено.")

        _st_code, st_out, _ = await _git(base, "status", "--short")
        uncommitted = [x for x in st_out.splitlines() if x.strip()]
        if uncommitted:
            lines.append(f"\nНезакоммиченные изменения ({len(uncommitted)}):")
            lines.extend(f"- {x}" for x in uncommitted)
            _ds_code, ds_out, _ = await _git(base, "diff", "--stat")
            if ds_out.strip():
                lines.append("\nСтатистика:")
                lines.append(ds_out.strip())
            _d_code, d_out, _ = await _git(base, "diff")
            diff = d_out.strip()
            if diff:
                if len(diff) > MAX_DIFF_CHARS:
                    total = len(d_out.strip())
                    diff = diff[:MAX_DIFF_CHARS] + f"\n… (обрезано, всего {total} символов)"
                lines.append("\nДифф:")
                lines.append(diff)
        else:
            lines.append("\nНезакоммиченных изменений нет.")
        return "\n".join(lines)

    async def _llm_summarize(text: str, max_sentences: int, model_id: str | None) -> str:
        """Природно-языковой вывод через LLM (по образцу mcp_scheduler)."""
        from agent.core.message import ChatRequest, Message, Role

        assert config is not None and llm is not None
        resolved = model_id or config.default_model
        provider, model = config.resolve_model(resolved)
        system = (
            "Ты — ревьюер изменений кода. На основе отчёта об изменениях "
            "напиши краткую содержательную выжимку на русском. Обязательно опиши:\n"
            "- что изменилось функционально и в каких частях кода (по ключевым "
            "моментам, без деталей);\n"
            "- незакоммиченные изменения отдельно: что находится в работе и в каком "
            "состоянии."
        )
        user = (
            f"Вот отчёт об изменениях:\n\n{text}\n\n"
            f"Сожми это в краткую выжимку (до {max_sentences} предложений)."
        )
        request = ChatRequest(
            model=model,
            messages=[
                Message(role=Role.SYSTEM, content=system),
                Message(role=Role.USER, content=user),
            ],
            stream=True,
        )
        parts: list[str] = []
        async for chunk in llm.astream(request, provider.api_base, provider.api_key):
            if chunk.content:
                parts.append(chunk.content)
        return "".join(parts).strip()

    async def _summarize(
        text: str, max_sentences: int = 20, model: str | None = None
    ) -> str:
        """Сжимает текст: LLM-сводка при наличии config/llm, иначе extractive-срез."""
        if config is not None and llm is not None:
            return await _llm_summarize(text, max_sentences, model)
        sentences = _split_sentences(text)
        if not sentences:
            return ""
        return " ".join(sentences[:max_sentences])

    async def _save(content: str, root: str | None = None) -> str:
        """Сохраняет `content` в `root/<дата_время>.md` и возвращает подтверждение."""
        base = Path(root or PROJECT_LOG).expanduser().resolve()
        name = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.md"
        base.mkdir(parents=True, exist_ok=True)
        target = base / name
        target.write_text(content, encoding="utf-8")
        return f"Сохранено {len(content)} символов в {name}"

    class _PipelineTool:
        """Обёртка внутренней реализации инструмента как `Tool` для `Pipeline`."""

        def __init__(
            self,
            name: str,
            description: str,
            parameters: dict[str, Any],
            impl: Any,
        ) -> None:
            self.name = name
            self.description = description
            self.parameters = parameters
            self._impl = impl

        async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
            try:
                return ToolResult(output=await self._impl(**arguments))
            except Exception as exc:
                return ToolResult(output=f"Ошибка инструмента: {exc}", is_error=True)

    server = MCPServer(
        "pipeline",
        version="0.1.0",
        instructions=(
            "Ревью изменений кода за последние сутки, сжатие и сохранение отчёта в project_log."
        ),
    )

    @server.tool()
    async def review_changes(
        days: Annotated[
            int, Field(default=1, ge=1, le=30, description="За сколько суток смотреть изменения")
        ] = 1,
        repo: Annotated[str, Field(default=".", description="Путь к git-репозиторию")] = ".",
    ) -> str:
        """Отчёт об изменениях кода в репозитории за последние сутки."""
        return await _review(days, repo)

    @server.tool()
    async def summarize(
        text: Annotated[str, Field(description="Текст для сжатия в краткую выжимку")],
        max_sentences: Annotated[
            int, Field(default=20, ge=1, le=50, description="Верхняя граница предложений в выжимке")
        ] = 20,
        model: Annotated[
            str | None, Field(default=None, description="Ид модели LLM (provider:model)")
        ] = None,
    ) -> str:
        """Сжимает текст в краткую выжимку (LLM или extractive)."""
        return await _summarize(text, max_sentences, model)

    @server.tool()
    async def saveToFile(
        content: Annotated[str, Field(description="Содержимое для сохранения")],
        root: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Целевой каталог (по умолчанию project_log). "
                    "Имя файла — дата/время создания, формат .md"
                ),
            ),
        ] = None,
    ) -> str:
        """Сохраняет `content` в файл `<дата_время>.md` внутри целевого каталога."""
        return await _save(content, root)

    @server.tool()
    async def run_pipeline(
        steps: Annotated[
            list[dict[str, Any]],
            Field(
                description=(
                    "Список шагов цепочки: "
                    "[{'tool': 'review_changes'|'summarize'|'saveToFile', 'params': {...}, "
                    "'label': ...}]."
                    " В строковых значениях params поддерживается ${prev} — вывод предыдущего шага."
                    " saveToFile сохраняет в файл <дата_время>.md (params: content, root)."
                )
            ),
        ],
    ) -> str:
        """Исполняет всю цепочку инструментов одним вызовом (данные текут между шагами)."""
        registry = ToolRegistry()
        registry.register(
            _PipelineTool(
                "review_changes",
                "Отчёт об изменениях",
                {"type": "object", "properties": {}},
                _review,
            )
        )
        registry.register(
            _PipelineTool(
                "summarize", "Краткая выжимка", {"type": "object", "properties": {}}, _summarize
            )
        )
        registry.register(
            _PipelineTool(
                "saveToFile", "Сохранение файла", {"type": "object", "properties": {}}, _save
            )
        )
        pipeline = Pipeline(registry)
        parsed = [
            PipelineStep(
                tool=str(step["tool"]),
                params=dict(step.get("params") or {}),
                label=step.get("label"),
            )
            for step in steps
        ]
        ctx = ToolContext(session=None, agent=None, project_id="", run_id="")  # type: ignore[arg-type]
        results = await pipeline.run(parsed, ctx)
        return "\n".join(f"[{r.label}] {r.tool}: {r.output}" for r in results)

    return server


def make_http_app(config: Any = None, llm: Any = None) -> Starlette:
    """Streamable HTTP ASGI-приложение MCP-сервера `pipeline`."""
    return make_server(config=config, llm=llm).streamable_http_app()


def main(argv: list[str] | None = None) -> int:
    """Точка входа консольного скрипта `agent-mcp-pipeline`.

    По умолчанию — stdio-транспорт. С `--http` — Streamable HTTP на `--port`.
    Для LLM-сводок читает `config.json` (`--config`); `--no-llm` отключает их.
    """
    parser = argparse.ArgumentParser(description="MCP-сервер пайплайна ревью изменений")
    parser.add_argument("--http", action="store_true", help="запустить Streamable HTTP")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP-порт (--http)")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="путь к config.json")
    parser.add_argument(
        "--no-llm", action="store_true", help="отключить LLM-сводку (extractive-срез)"
    )
    args = parser.parse_args(argv)

    config: Any = None
    llm: Any = None
    if not args.no_llm:
        from agent.config.store import load_config
        from agent.llm.client import LLMClient

        config = load_config(args.config)
        llm = LLMClient()

    if args.http:
        uvicorn.run(make_http_app(config, llm), host="127.0.0.1", port=args.port, log_level="info")
        return 0
    asyncio.run(make_server(config=config, llm=llm).run_stdio_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
