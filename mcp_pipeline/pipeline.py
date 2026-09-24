"""Pipeline — автоматическая цепочка инструментов.

Последовательно исполняет инструменты из `ToolRegistry` и пробрасывает вывод
предыдущего шага в аргументы следующего через плейсхолдер `${prev}`. Это
позволяет собрать «пайплайн» из нескольких отдельных инструментов (например,
`search` -> `summarize` -> `saveToFile`) и выполнить его одним вызовом без
участия LLM: цепочка исполняется автоматически, данные текут между шагами.

Строка `${prev}` может встречаться в любом строковом значении аргумента шага —
при исполнении она заменяется на вывод предыдущего шага. Цепочка останавливается
на первом шаге с ошибкой (инструмент не найден, вернул `is_error` или упал).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

from agent.tools.context import ToolContext
from agent.tools.registry import ToolRegistry

__all__ = ["Pipeline", "PipelineResult", "PipelineStep"]

_PREV = "${prev}"


class PipelineStep(BaseModel):
    """Один шаг цепочки: имя инструмента, аргументы и необязательная метка.

    В аргументах поддерживается плейсхолдер `${prev}` — он подменяется выводом
    предыдущего шага (данные передаются между инструментами).
    """

    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    label: str | None = Field(default=None, description="Необязательная метка шага")

    def bind(self, prev: str | None) -> dict[str, Any]:
        """Подставляет `${prev}` в строковые значения аргументов (`prev` — вывод прошлого шага)."""
        if prev is None:
            return dict(self.params)
        bound: dict[str, Any] = {}
        for key, value in self.params.items():
            if isinstance(value, str) and _PREV in value:
                bound[key] = value.replace(_PREV, prev)
            else:
                bound[key] = value
        return bound


class PipelineResult(BaseModel):
    """Результат одного шага: метка, инструмент, вывод и признак ошибки."""

    label: str
    tool: str
    output: str
    is_error: bool = False


class Pipeline:
    """Исполняет цепочку шагов автоматически, пробрасывая вывод между шагами.

    `registry` предоставляет инструменты по имени (статичные либо динамические,
    например MCP-инструменты, синхронизированные через `McpAdapter`).
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def run(
        self, steps: Sequence[PipelineStep], ctx: ToolContext
    ) -> list[PipelineResult]:
        """Выполняет все шаги по порядку и возвращает результаты каждого шага."""
        results: list[PipelineResult] = []
        prev: str | None = None
        for step in steps:
            label = step.label or step.tool
            tool = self._registry.get(step.tool)
            if tool is None:
                results.append(
                    PipelineResult(
                        label=label,
                        tool=step.tool,
                        output=f"инструмент '{step.tool}' не найден",
                        is_error=True,
                    )
                )
                break
            params = step.bind(prev)
            try:
                result = await tool.execute(params, ctx)
            except Exception as exc:  # падение инструмента не роняет пайплайн
                results.append(
                    PipelineResult(
                        label=label,
                        tool=step.tool,
                        output=f"ошибка инструмента: {exc}",
                        is_error=True,
                    )
                )
                break
            results.append(
                PipelineResult(
                    label=label, tool=step.tool, output=result.output, is_error=result.is_error
                )
            )
            if result.is_error:
                break
            prev = result.output
        return results
