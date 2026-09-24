"""Реестр агентов бэкенда: единственные инстансы общих ресурсов + персист + DTO.

Один `WebState` на процесс. Агенты — по одному на вкладку фронтенда;
`agent_id` (uuid) стабилен и не зависит от сессии автосохранения
`session_id`. Общие _LLM-клиент, tools, config, store — shared.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from agent.commands.registry import CommandRegistry
from agent.config.schema import AgentSettings, Config
from agent.core.agent import Agent, AgentBusyError, TokenUsage
from agent.core.message import Message, Role
from agent.core.task import TaskPhase, TaskState
from agent.llm.client import LLMClient
from agent.memory.longterm import ProjectLongTermMemory
from agent.memory.persistence import ProfileInfo, ProjectInfo, SessionStore
from agent.tools.delegate import delegate_tools
from agent.tools.mcp_manager import McpManager, McpStatusTool
from agent.tools.registry import Tool, ToolRegistry
from agent.web_server.dto import (
    AgentDTO,
    AgentSettingsDTO,
    CommandDTO,
    ConfigDTO,
    LongTermDTO,
    McpDTO,
    MessageDTO,
    ProfileDTO,
    ProjectDTO,
    ProviderDTO,
    SchedulerEventRequest,
    SessionInfoDTO,
    SystemPromptDTO,
    TaskCommandOperation,
    TaskCommandRequest,
    TaskStateDTO,
    UsageDTO,
)

DEFAULT_SYSTEM_PROMPT_PATH = "SYSTEM_PROMPT.md"
DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_NAME = "По умолчанию"


@dataclass
class AgentRecord:
    """Запись агента в реестре: агент + его сессия автосохранения."""

    agent_id: str
    agent: Agent
    session_id: str
    system_prompt_path: str
    project_id: str = ""
    fingerprint: tuple[object, ...] = field(default_factory=tuple)


class WebState:
    """Реестр агентов и общие ресурсы (единственные инстансы на процесс)."""

    def __init__(
        self,
        *,
        config: Config,
        llm: LLMClient,
        tools: ToolRegistry,
        store: SessionStore,
        default_system_prompt: str,
        default_prompt_path: str = DEFAULT_SYSTEM_PROMPT_PATH,
        mcp: McpManager | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = tools
        self.store = store
        self.mcp = mcp
        self.default_project_id = DEFAULT_PROJECT_ID
        self._default_system_prompt = default_system_prompt
        self._default_prompt_path = default_prompt_path
        self._records: dict[str, AgentRecord] = {}
        self.active_agent_id: str | None = None
        # проект по умолчанию всегда существует (верхний уровень иерархии памяти)
        self.store.ensure_project(DEFAULT_PROJECT_ID, DEFAULT_PROJECT_NAME)

    # --- доступ к записям ---

    @property
    def records(self) -> dict[str, AgentRecord]:
        return self._records

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._records.get(agent_id)

    def active_agent(self) -> AgentRecord | None:
        """Активный агент: last-active, fallback — первый агент."""
        if self._records:
            if self.active_agent_id in self._records:
                return self._records[self.active_agent_id or ""]
            return next(iter(self._records.values()))
        return None

    def set_active(self, agent_id: str) -> None:
        if agent_id in self._records:
            self.active_agent_id = agent_id

    # --- жизненный цикл агентов ---

    def create_agent(self, name: str | None = None, project_id: str | None = None) -> AgentRecord:
        """Создаёт нового агента с дефолтными настройками и регистрирует его."""
        if project_id is None:
            project_id = self.default_project_id
        if self.store.get_project(project_id) is None:
            raise KeyError(f"проект не найден: {project_id}")
        settings = AgentSettings.from_config(self.config)
        agent_id = self.store.new_id()
        session_id = self.store.new_id()
        agent = Agent(
            name=name or f"chat-{len(self._records) + 1}",
            settings=settings,
            system_prompt=self._default_system_prompt,
            llm=self.llm,
            config=self.config,
            tools=self.tools,
            longterm=ProjectLongTermMemory(self.store, project_id),
            project_id=project_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        record = AgentRecord(
            agent_id=agent_id,
            agent=agent,
            session_id=session_id,
            system_prompt_path=self._default_prompt_path,
            project_id=project_id,
        )
        self._records[agent_id] = record
        if self.active_agent_id is None:
            self.active_agent_id = agent_id
        record.agent.register_tools(self._delegate_tools(record))
        self._register_mcp_status(record)
        self._sync_record_mcp(record)
        self.persist(record)
        return record

    def delete_agent(self, agent_id: str) -> bool:
        """Удаляет агента и его сессию. 409 — если агент стримит."""
        record = self._records.get(agent_id)
        if record is None:
            return False
        if record.agent.is_streaming:
            raise AgentBusyError("агент сейчас отвечает")
        self.store.delete(record.session_id)
        del self._records[agent_id]
        if self.active_agent_id == agent_id:
            self.active_agent_id = None
        return True

    # --- действия над сохранёнными сессиями (палитра / боковая панель) ---

    def delete_session(self, session_id: str) -> bool:
        """Удаляет сохранённую сессию из хранилища. True, если она была."""
        return self.store.delete(session_id)

    def branch_session(self, session_id: str) -> AgentRecord:
        """Создаёт нового агента-копию сессии (ветку от сохранённого чата).

        Сессия читается из хранилища, агент применяет её состояние как свою
        стартовую точку, но получает свежий `session_id`, чтобы ветка не
        перезаписывала исходник при автосохранении. KeyError — сессии нет.
        """
        data = self.store.get(session_id)
        if data is None:
            raise KeyError(f"сессия не найдена: {session_id}")
        project_id = self.store.get_project_id(session_id) or self.default_project_id
        agent_id = self.store.new_id()
        branch_session_id = self.store.new_id()
        agent = Agent(
            name=data.name or f"chat-{len(self._records) + 1}",
            settings=data.settings,
            system_prompt=data.system_prompt,
            llm=self.llm,
            config=self.config,
            tools=self.tools,
            longterm=ProjectLongTermMemory(self.store, project_id),
            project_id=project_id,
            agent_id=agent_id,
            session_id=branch_session_id,
        )
        agent.apply_session(data)
        record = AgentRecord(
            agent_id=agent_id,
            agent=agent,
            system_prompt_path=self._default_prompt_path,
            session_id=branch_session_id,
            project_id=project_id,
        )
        self._apply_profile_content(record)
        self._records[agent_id] = record
        self.active_agent_id = agent_id
        record.agent.register_tools(self._delegate_tools(record))
        self._register_mcp_status(record)
        self._sync_record_mcp(record)
        self.persist(record)
        return record

    def load_session(self, agent_id: str, session_id: str) -> AgentRecord:
        """Загружает сохранённую сессию в агента, перевязывая его на её проект."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        data = self.store.get(session_id)
        if data is None:
            raise KeyError(f"сессия не найдена: {session_id}")
        project_id = self.store.get_project_id(session_id) or self.default_project_id
        record.agent.set_project(project_id, ProjectLongTermMemory(self.store, project_id))
        record.agent.apply_session(data)
        self._apply_profile_content(record)
        record.agent.register_tools(self._delegate_tools(record))
        self._register_mcp_status(record)
        self._sync_record_mcp(record)
        record.session_id = session_id
        record.agent.agent_id = record.agent_id
        record.agent.session_id = session_id
        record.project_id = project_id
        self.set_active(agent_id)
        self.persist(record)
        return record

    def _apply_profile_content(self, record: AgentRecord) -> None:
        """Подтягивает текст активного профиля агента из store в `profile_content`."""
        profile_id = record.agent.active_profile_id
        if not profile_id:
            record.agent.set_active_profile("", "")
            return
        profile = self.store.get_profile(profile_id)
        if profile is None:
            record.agent.set_active_profile("", "")
            return
        record.agent.set_active_profile(profile_id, profile.content)

    def _delegate_tools(self, record: AgentRecord) -> list[Tool]:
        """Инструменты делегирования для агента, привязанные к его проекту (live)."""
        return delegate_tools(record.agent, self.store, self.llm, self.config)

    def _register_mcp_status(self, record: AgentRecord) -> None:
        """Регистрирует инструмент `mcp_status` (статистика MCP), если MCP настроен."""
        if self.mcp is None:
            return
        record.agent.register_tools([McpStatusTool(self.mcp)])

    def _sync_record_mcp(self, record: AgentRecord) -> None:
        """Пересобирает динамические MCP-инструменты агента из менеджера."""
        if self.mcp is None:
            return
        record.agent.sync_dynamic_tools(self.mcp.enabled_tools())

    def _sync_all_mcp(self) -> None:
        """Пересобирает MCP-инструменты у всех агентов."""
        for record in self._records.values():
            self._sync_record_mcp(record)

    async def start_mcp(self) -> None:
        """Пробинг всех MCP-серверов в фоне при старте + синк в существующие агенты."""
        if self.mcp is None:
            return
        await self.mcp.connect_all()
        self._sync_all_mcp()

    async def set_mcp_enabled(self, name: str, enabled: bool) -> list[McpDTO]:
        """Глобальный вкл/выкл MCP-сервера + пере-синк инструментов у агентов."""
        if self.mcp is None:
            raise KeyError("MCP не настроен")
        await self.mcp.set_enabled(name, enabled)
        self._sync_all_mcp()
        return self.list_mcp()

    def list_mcp(self) -> list[McpDTO]:
        """Состояние всех MCP-серверов для панели."""
        return self.mcp.dto_list() if self.mcp is not None else []

    # --- персист (аналог TUI _persist_tab/_tick) ---

    @staticmethod
    def _fingerprint(record: AgentRecord) -> tuple[object, ...]:
        """Слепок состояния агента: совпадает — ничего не изменилось, снапшот не нужен."""
        agent = record.agent
        memory = agent.memory
        history = memory.history
        last = history[-1].to_api() if history else None
        settings = agent.settings
        return (
            len(history),
            last,
            agent.name,
            settings.model,
            settings.temperature,
            settings.top_p,
            settings.max_tokens,
            tuple(settings.stop),
            agent.system_prompt,
            memory.summary,
            settings.context_strategy,
            settings.sliding_window,
            memory.active_branch,
            len(memory.branches),
            tuple(sorted(memory.facts.items())),
            memory.scratchpad,
            tuple(memory.invariants),
            json.dumps(agent.memory.task.state.to_dict(), sort_keys=True, ensure_ascii=False),
            agent.active_profile_id,
        )

    def persist(self, record: AgentRecord) -> None:
        """Snapshot сессии агента в SQLite (атомарный upsert)."""
        agent = record.agent
        self.store.snapshot(
            record.session_id,
            agent.name,
            agent.settings,
            agent.system_prompt,
            agent.memory.history,
            summary=agent.memory.summary,
            facts=agent.memory.facts,
            scratchpad=agent.memory.scratchpad,
            invariants=agent.memory.invariants,
            active_branch=agent.memory.active_branch,
            branches=agent.memory.branches,
            project_id=agent.project_id,
            active_profile_id=agent.active_profile_id,
            task=agent.memory.task.state,
        )
        record.fingerprint = self._fingerprint(record)

    def persist_if_dirty(self) -> None:
        """Тик автосохранения: снапшот тех агентов, чей слепок изменился."""
        for record in self._records.values():
            if record.fingerprint != self._fingerprint(record):
                self.persist(record)

    # --- планировщик: уведомления о заданиях и их результатах ---

    def handle_scheduler_event(self, body: SchedulerEventRequest) -> None:
        """Обработка webhook-события планировщика (job_added / job_removed / job_ran).

        job_added/job_removed — меняют счётчик активных заданий сессии;
        job_ran — доставляет результат в сессию создателя: если сессия сейчас
        загружена (live-агент), сообщение попадает в его историю, иначе
        записывается напрямую в хранилище сессии. В обоих случаях растёт
        счётчик непрочитанных сообщений.
        """
        session_id = str(body.job.get("owner_session_id", ""))
        if not session_id:
            return
        if body.event == "job_added":
            self.store.incr_scheduled(session_id)
        elif body.event == "job_removed":
            self.store.decr_scheduled(session_id)
        elif body.event == "job_ran":
            self._deliver_scheduler_result(session_id, body)

    def _owner_record(self, session_id: str) -> AgentRecord | None:
        """Live-агент, чья сессия автосохранения совпадает с session_id."""
        for record in self._records.values():
            if record.session_id == session_id:
                return record
        return None

    def _deliver_scheduler_result(self, session_id: str, body: SchedulerEventRequest) -> None:
        """Формирует сообщение-результат и доставляет его в сессию создателя."""
        text = self._format_scheduler_result(body)
        message_json = Message(role=Role.ASSISTANT, content=text).model_dump_json()
        record = self._owner_record(session_id)
        if record is not None:
            record.agent.memory.add(Message(role=Role.ASSISTANT, content=text))
            self.persist(record)
        else:
            self.store.append_notification(session_id, message_json)
        self.store.incr_unread(session_id)

    @staticmethod
    def _format_scheduler_result(body: SchedulerEventRequest) -> str:
        """Русский текст результата по типу задания."""
        job = body.job
        kind = str(job.get("kind", ""))
        summary = body.summary or {}
        name = str(job.get("name", ""))
        title = f"⏰ Задание «{name}»"
        if kind == "reminder":
            note = summary.get("note")
            return f"{title}\n{note}" if note else title
        if kind == "collect":
            data = summary.get("data")
            if data:
                return f"{title}: собраны данные: {data}"
            return f"{title}: данные собраны"
        if kind == "summary":
            aggregate = summary.get("aggregate")
            llm = summary.get("llm")
            if llm:
                return f"{title}: {llm}"
            if aggregate:
                return f"{title}: {aggregate}"
            return f"{title}: сводка готова"
        if "error" in summary:
            return f"{title}: ошибка — {summary['error']}"
        return title

    def mark_session_read(self, session_id: str) -> None:
        """Сбрасывает счётчик непрочитанных результатов сессии."""
        self.store.clear_unread(session_id)

    # --- долговременная память (по слою проекта — Слой 1) ---

    def _project_memory(self, project_id: str | None = None) -> ProjectLongTermMemory:
        """Долговременная память проекта; по умолчанию — проект по умолчанию."""
        if project_id is None:
            project_id = self.default_project_id
        return ProjectLongTermMemory(self.store, project_id)

    def longterm_dto(self, project_id: str | None = None) -> LongTermDTO:
        """Текущее содержимое долговременной памяти проекта."""
        memory = self._project_memory(project_id)
        entries = memory.entries()
        return LongTermDTO(
            project_id=memory.project_id,
            path="",
            content=memory.load(),
            entries=entries,
        )

    def remember(self, text: str, project_id: str | None = None) -> LongTermDTO:
        """Добавляет знание в долговременную память проекта."""
        self._project_memory(project_id).append(text)
        return self.longterm_dto(project_id)

    def forget(self, index: int, project_id: str | None = None) -> LongTermDTO:
        """Удаляет запись долговременной памяти проекта по индексу."""
        self._project_memory(project_id).remove(index)
        return self.longterm_dto(project_id)

    def update_longterm(self, index: int, text: str, project_id: str | None = None) -> LongTermDTO:
        """Заменяет запись долговременной памяти проекта по индексу."""
        self._project_memory(project_id).update(index, text)
        return self.longterm_dto(project_id)

    def accept_suggestion(self, agent_id: str) -> LongTermDTO:
        """Принять предложение агента: знание — в долговременную память его проекта."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        suggestion = record.agent.pending_memory_suggestion
        if suggestion is None:
            raise ValueError("нет предложения для сохранения")
        result = self.remember(suggestion, record.agent.project_id)
        record.agent.dismiss_suggestion()
        return result

    def fork_at(self, agent_id: str, message_index: int) -> AgentRecord:
        """Ветка от сообщения: имя генерируется автоматически (branch-1, branch-2…)."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        if record.agent.is_streaming:
            raise AgentBusyError("агент сейчас отвечает")
        names = set(record.agent.memory.branch_names())
        n = 1
        while f"branch-{n}" in names:
            n += 1
        try:
            record.agent.fork_at(f"branch-{n}", message_index)
        except (ValueError, IndexError) as exc:
            raise KeyError(str(exc)) from exc
        self.persist(record)
        return record

    # --- DTO-мапперы ---

    def agent_dto(self, record: AgentRecord) -> AgentDTO:
        agent = record.agent
        settings = agent.settings
        context_now = agent.context_now
        return AgentDTO(
            id=record.agent_id,
            name=agent.name,
            model=settings.model,
            settings=AgentSettingsDTO(
                temperature=settings.temperature,
                top_p=settings.top_p,
                max_tokens=settings.max_tokens,
                stop=list(settings.stop),
                context_strategy=settings.context_strategy,
                sliding_window=settings.sliding_window,
                compaction_threshold=settings.compaction_threshold,
            ),
            system_prompt=SystemPromptDTO(
                path=record.system_prompt_path,
                content=agent.system_prompt,
            ),
            project_id=agent.project_id,
            context_used=context_now[0],
            context_window=agent.context_window,
            streaming=agent.is_streaming,
            compacting=agent.is_compacting or agent.is_extracting_facts,
            scratchpad=agent.memory.scratchpad,
            memory_suggestion=agent.pending_memory_suggestion,
            active_profile_id=agent.active_profile_id,
            task=WebState.task_dto(agent.memory.task.state),
            invariants=agent.memory.invariants,
        )

    @staticmethod
    def task_dto(state: TaskState) -> TaskStateDTO | None:
        """Состояние задачи для DTO; None — задачи нет (этап idle)."""
        if not state.is_active:
            return None
        return TaskStateDTO(
            phase=state.phase.value,
            step=state.step,
            steps=list(state.steps),
            validation_steps=list(state.validation_steps),
            expected_action=state.expected_action,
            description=state.description,
            paused=state.paused,
            plan_confirmed=state.plan_confirmed,
        )

    def config_dto(self) -> ConfigDTO:
        config = self.config
        providers: dict[str, ProviderDTO] = {}
        for name, provider in config.providers.items():
            models: dict[str, dict[str, int]] = {}
            for model, window in provider.models.items():
                models[model] = (
                    {"context_window": window} if window is not None else {}
                )
            providers[name] = ProviderDTO(api_base=provider.api_base, models=models)
        return ConfigDTO(
            providers=providers,
            default_model=config.default_model,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            stop=list(config.stop),
            context_window_default=config.context_window_default,
            compaction_threshold=config.compaction_threshold,
            sliding_window=config.sliding_window,
        )

    def commands_dto(self, commands: CommandRegistry) -> list[CommandDTO]:
        items = []
        for command in commands.all():
            items.append(
                CommandDTO(
                    name=command.name,
                    description=command.description,
                    args_spec=command.args_spec,
                )
            )
        return items

    @staticmethod
    def usage_dto(usage: TokenUsage | None) -> UsageDTO | None:
        if usage is None:
            return None
        return UsageDTO(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            approx=usage.estimated,
        )

    @staticmethod
    def message_dto(message: Message, idx: int, usage: UsageDTO | None = None) -> MessageDTO:
        return MessageDTO(
            id=f"m{idx}",
            role=message.role.value,
            content=message.content or "",
            reasoning=message.reasoning,
            usage=usage,
            tool_name=message.name,
            tool_calls=(
                [call.function.name for call in message.tool_calls]
                if message.tool_calls is not None
                else None
            ),
        )

    def history_dto(self, record: AgentRecord) -> list[MessageDTO]:
        messages: list[MessageDTO] = []
        agent = record.agent
        for idx, message in enumerate(agent.memory.history):
            usage = None
            if message.role is Role.ASSISTANT:
                token_usage = agent.message_usage.get(idx)
                usage = self.usage_dto(token_usage)
            messages.append(self.message_dto(message, idx, usage))
        return messages

    def sessions_dto(
        self,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[SessionInfoDTO]:
        return [
            SessionInfoDTO(
                id=info.id,
                title=info.title,
                updated_at=info.updated_at,
                model=info.model,
                message_count=info.message_count,
                project_id=info.project_id,
                has_scheduled=info.has_scheduled,
                unread_notifications=info.unread_notifications,
            )
            for info in self.store.list(limit=limit, offset=offset, project_id=project_id)
        ]

    # --- проекты (Слой 1) ---

    def project_dto(self, info: ProjectInfo) -> ProjectDTO:
        return ProjectDTO(
            id=info.id,
            name=info.name,
            session_count=info.session_count,
            updated_at=info.updated_at,
            profile_ids=self.store.list_project_profiles(info.id),
        )

    def list_projects(self) -> list[ProjectDTO]:
        return [self.project_dto(info) for info in self.store.list_projects()]

    def get_project(self, project_id: str) -> ProjectDTO | None:
        info = self.store.get_project(project_id)
        return self.project_dto(info) if info is not None else None

    def create_project(self, name: str) -> ProjectDTO:
        return self.project_dto(self.store.create_project(name))

    def rename_project(self, project_id: str, name: str) -> bool:
        return self.store.rename_project(project_id, name)

    def delete_project(self, project_id: str) -> bool:
        """Удаляет проект вместе с его сессиями и долговременной памятью."""
        if self.store.delete_project(project_id):
            removed = [
                agent_id
                for agent_id, record in self._records.items()
                if record.agent.project_id == project_id
            ]
            for agent_id in removed:
                del self._records[agent_id]
            if self.active_agent_id in removed:
                self.active_agent_id = None
            return True
        return False

    # --- профили (глобальный пул + привязка к проекту) ---

    @staticmethod
    def profile_dto(info: ProfileInfo) -> ProfileDTO:
        return ProfileDTO(id=info.id, name=info.name, content=info.content)

    def list_profiles(self) -> list[ProfileDTO]:
        return [self.profile_dto(info) for info in self.store.list_profiles()]

    def get_profile(self, profile_id: str) -> ProfileDTO | None:
        info = self.store.get_profile(profile_id)
        return self.profile_dto(info) if info is not None else None

    def create_profile(self, name: str, content: str) -> ProfileDTO:
        try:
            return self.profile_dto(self.store.create_profile(name, content))
        except ValueError as exc:
            raise KeyError(str(exc)) from exc

    def update_profile(self, profile_id: str, name: str, content: str) -> ProfileDTO:
        try:
            info = self.store.update_profile(profile_id, name, content)
        except ValueError as exc:
            raise KeyError(str(exc)) from exc
        if info is None:
            raise KeyError(f"профиль не найден: {profile_id}")
        return self.profile_dto(info)

    def delete_profile(self, profile_id: str) -> bool:
        """Удаляет профиль; у агентов, ссылающихся на него, снимает активный профиль."""
        if not self.store.delete_profile(profile_id):
            return False
        for record in self._records.values():
            if record.agent.active_profile_id == profile_id:
                record.agent.set_active_profile("", "")
        return True

    def project_profiles_dto(self, project_id: str) -> list[ProfileDTO]:
        """Профили, привязанные к проекту."""
        if self.store.get_project(project_id) is None:
            raise KeyError(f"проект не найден: {project_id}")
        profiles: list[ProfileDTO] = []
        for profile_id in self.store.list_project_profiles(project_id):
            info = self.store.get_profile(profile_id)
            if info is not None:
                profiles.append(self.profile_dto(info))
        return profiles

    def set_project_profile_ids(self, project_id: str, profile_ids: list[str]) -> list[ProfileDTO]:
        """Заменяет набор профилей проекта (валидирует их существование)."""
        if self.store.get_project(project_id) is None:
            raise KeyError(f"проект не найден: {project_id}")
        for profile_id in profile_ids:
            if self.store.get_profile(profile_id) is None:
                raise KeyError(f"профиль не найден: {profile_id}")
        self.store.set_project_profiles(project_id, profile_ids)
        allowed = set(profile_ids)
        for record in self._records.values():
            if (
                record.agent.project_id == project_id
                and record.agent.active_profile_id
                and record.agent.active_profile_id not in allowed
            ):
                record.agent.set_active_profile("", "")
        return self.project_profiles_dto(project_id)

    def set_active_profile(self, agent_id: str, profile_id: str) -> AgentRecord:
        """Назначает агенту активный профиль ("" — снять). Профиль должен быть в проекте."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        if profile_id:
            if self.store.get_profile(profile_id) is None:
                raise KeyError(f"профиль не найден: {profile_id}")
            project_profiles = self.store.list_project_profiles(record.agent.project_id)
            if profile_id not in project_profiles:
                raise KeyError("профиль не привязан к проекту агента")
            profile = self.store.get_profile(profile_id)
            assert profile is not None
            record.agent.set_active_profile(profile_id, profile.content)
        else:
            record.agent.set_active_profile("", "")
        self.persist(record)
        return record

    def apply_task_command(self, agent_id: str, body: TaskCommandRequest) -> AgentRecord:
        """Применяет команду к автомату задачи агента и персистит изменение.

        Несуществующий агент — KeyError; недопустимый переход — InvalidTaskTransition;
        неверный этап/пустое действие — ValueError.
        """
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        machine = record.agent.memory.task
        operation = body.operation
        if operation is TaskCommandOperation.START:
            machine.start(
                body.description,
                body.steps,
                validation_steps=body.validation_steps,
                expected_action=body.expected_action,
            )
        elif operation is TaskCommandOperation.SET_PHASE:
            machine.transition(TaskPhase(body.phase), expected_action=body.expected_action)
        elif operation is TaskCommandOperation.ADVANCE:
            machine.advance_step(body.expected_action, done=body.done)
        elif operation is TaskCommandOperation.PAUSE:
            machine.pause()
        elif operation is TaskCommandOperation.RESUME:
            machine.resume()
        elif operation is TaskCommandOperation.RESET:
            machine.reset()
        elif operation is TaskCommandOperation.SET_EXPECTED_ACTION:
            if not body.expected_action:
                raise ValueError("expected_action не может быть пустым")
            machine.set_expected_action(body.expected_action)
        elif operation is TaskCommandOperation.CONFIRM_PLAN:
            if machine.state.phase is not TaskPhase.PLANNING:
                raise ValueError("подтвердить план можно только из фазы планирования")
            if not machine.state.steps:
                raise ValueError("план пуст — сначала составь план")
            machine.set_plan_confirmed(True)
            machine.transition(
                TaskPhase.EXECUTION,
                expected_action=body.expected_action or None,
                note="план подтверждён пользователем",
            )
        self.persist(record)
        return record
