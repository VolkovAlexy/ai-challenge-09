"""Инструменты делегирования: оркестратор вызывает субагентов (профили проекта).

Идея: оркестратор — это любой агент, у которого в проекте есть профили-роли
(писатель, редактор, консультант...). Инструмент `delegate` создаёт эфемерного
субагента: его системный промпт — текст выбранного профиля, свежая сессия без
истории. Субагент получает задачу+контекст от оркестратора, выполняет один ход
и возвращает ответ как результат инструмента. Каноническое состояние (задача,
scratchpad книги) остаётся у оркестратора.

Субагент создаётся БЕЗ инструментов делегирования (`tools=None`), поэтому
бесконечная рекурсия «оркестратор → субагент → …» невозможна по построению.
Субагент также НЕ получает инструменты конечного автомата задачи
(`with_task_tools=False`): он — чистый исполнитель, который получает задачу и
контекст от оркестратора и выполняет их без планирования и подтверждений.

`on_delta` пробрасывает потоковые дельты ответа субагента через
orchestrator.subagent_events — их дренирует SSE-стример (web), чтобы UI
показывал работу субагента как отдельный (сворачиваемый) блок.
"""

from __future__ import annotations

from typing import Any

from agent.config.schema import Config
from agent.core.agent import Agent
from agent.core.task import PHASE_LABELS, TaskPhase
from agent.llm.client import LLMClient, LLMError
from agent.memory.persistence import ProfileInfo, SessionStore
from agent.tools.context import ToolContext
from agent.tools.registry import Tool, ToolResult

_DELEGATE_PARAMS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "role": {
            "type": "string",
            "description": "Имя или id профиля-субагента из профилей текущего проекта",
        },
        "task": {
            "type": "string",
            "description": "Задача, которую субагент должен выполнить",
        },
        "context": {
            "type": "string",
            "description": "Контекст/данные, передаваемые субагенту (глава книги, правки и т.п.)",
        },
        "model": {
            "type": "string",
            "description": (
                "Модель субагента в формате provider:model (по умолчанию — модель оркестратора)"
            ),
        },
    },
    "required": ["role", "task"],
}

_LIST_PARAMS: dict[str, Any] = {"type": "object", "properties": {}}


class DelegateTool:
    """delegate: поручает задачу субагенту (профилю проекта) и возвращает его ответ."""

    name = "delegate"
    description = (
        "Поручить задачу субагенту (профилю проекта: писатель, редактор, консультант). "
        "Субагент получит твой task/context как задание, выполнит его и вернёт результат. "
        "Сам субагент не помнит предыдущие вызовы — передавай ему нужный контекст явно."
    )

    def __init__(
        self,
        orchestrator: Agent,
        store: SessionStore,
        llm: LLMClient,
        config: Config,
    ) -> None:
        self._orchestrator = orchestrator
        self._store = store
        self._llm = llm
        self._config = config
        self.parameters = _DELEGATE_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        role = str(arguments.get("role", "")).strip()
        task = str(arguments.get("task", "")).strip()
        if not role:
            return ToolResult(output="Ошибка: role не может быть пустым", is_error=True)
        if not task:
            return ToolResult(output="Ошибка: task не может быть пустым", is_error=True)
        profile = self._resolve_profile(role)
        if profile is None:
            return ToolResult(
                output=f"Ошибка: профиль '{role}' не найден среди профилей проекта",
                is_error=True,
            )
        phase = self._orchestrator.memory.task.state.phase
        if phase not in (TaskPhase.EXECUTION, TaskPhase.VALIDATION):
            label = PHASE_LABELS.get(phase.value, phase.value)
            return ToolResult(
                output=(
                    f"Ошибка: делегирование доступно только на этапах выполнения и "
                    f"проверки (сейчас — '{label}')"
                ),
                is_error=True,
            )
        settings = self._orchestrator.settings.model_copy(deep=True)
        model_arg = arguments.get("model")
        if model_arg:
            try:
                self._config.resolve_model(str(model_arg))
            except ValueError as exc:
                return ToolResult(output=f"Ошибка: {exc}", is_error=True)
            settings.model = str(model_arg)
        subagent = Agent(
            name=profile.name,
            settings=settings,
            system_prompt=profile.content,
            llm=self._llm,
            config=self._config,
            tools=None,
            longterm=None,
            project_id=self._orchestrator.project_id,
            with_task_tools=False,
        )
        context = arguments.get("context")
        prompt = f"{context}\n\n{task}" if context and str(context).strip() else task
        self._orchestrator.begin_subagent(profile.name)
        result: str = ""
        try:
            result = await subagent.ask(
                prompt, on_delta=lambda d: self._orchestrator.stream_subagent(profile.name, d)
            )
        except LLMError as exc:
            return ToolResult(output=f"Ошибка субагента: {exc}", is_error=True)
        except Exception as exc:
            return ToolResult(output=f"Ошибка субагента: {exc}", is_error=True)
        finally:
            self._orchestrator.end_subagent(profile.name)
        return ToolResult(output=result)

    def _resolve_profile(self, role: str) -> ProfileInfo | None:
        """Профиль субагента по id или имени, ограниченный профилями проекта."""
        for profile_id in self._store.list_project_profiles(self._orchestrator.project_id):
            profile = self._store.get_profile(profile_id)
            if profile is None:
                continue
            if profile.id == role or profile.name == role:
                return profile
        return None


class ListSubagentsTool:
    """list_subagents: перечисляет профили-субагенты текущего проекта."""

    name = "list_subagents"
    description = (
        "Список доступных профилей-субагентов (ролей) текущего проекта. "
        "Используй, чтобы узнать, кому можно делегировать задачи."
    )

    def __init__(self, orchestrator: Agent, store: SessionStore) -> None:
        self._orchestrator = orchestrator
        self._store = store
        self.parameters = _LIST_PARAMS

    async def execute(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        profiles: list[ProfileInfo] = []
        for profile_id in self._store.list_project_profiles(self._orchestrator.project_id):
            profile = self._store.get_profile(profile_id)
            if profile is not None:
                profiles.append(profile)
        if not profiles:
            return ToolResult(output="(в проекте нет профилей-субагентов)")
        lines = [f"- {profile.name} ({profile.id})" for profile in profiles]
        return ToolResult(output="\n".join(lines))


def delegate_tools(
    orchestrator: Agent, store: SessionStore, llm: LLMClient, config: Config
) -> list[Tool]:
    """Инструменты делегирования, привязанные к агенту-оркестратору."""
    return [
        DelegateTool(orchestrator, store, llm, config),
        ListSubagentsTool(orchestrator, store),
    ]
