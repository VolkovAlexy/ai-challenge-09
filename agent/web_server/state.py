"""Реестр агентов бэкенда: единственные инстансы общих ресурсов + персист + DTO.

Один `WebState` на процесс. Агенты — по одному на вкладку фронтенда;
`agent_id` (uuid) стабилен и не зависит от сессии автосохранения
`session_id`. Общие _LLM-клиент, tools, config, store — shared.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.commands.registry import CommandRegistry
from agent.config.schema import AgentSettings, Config
from agent.core.agent import Agent, AgentBusyError, TokenUsage
from agent.core.message import Message, Role
from agent.llm.client import LLMClient
from agent.memory.longterm import LongTermMemory
from agent.memory.persistence import SessionStore
from agent.tools.registry import ToolRegistry
from agent.web_server.dto import (
    AgentDTO,
    AgentSettingsDTO,
    CommandDTO,
    ConfigDTO,
    LongTermDTO,
    MessageDTO,
    ProviderDTO,
    SessionInfoDTO,
    SystemPromptDTO,
    UsageDTO,
)

DEFAULT_SYSTEM_PROMPT_PATH = "SYSTEM_PROMPT.md"
DEFAULT_LONGTERM_PATH = "LONGTERM_MEMORY.md"


@dataclass
class AgentRecord:
    """Запись агента в реестре: агент + его сессия автосохранения."""

    agent_id: str
    agent: Agent
    session_id: str
    system_prompt_path: str
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
        longterm: LongTermMemory | None = None,
    ) -> None:
        self.config = config
        self.llm = llm
        self.tools = tools
        self.store = store
        self.longterm = longterm
        self._default_system_prompt = default_system_prompt
        self._default_prompt_path = default_prompt_path
        self._records: dict[str, AgentRecord] = {}
        self.active_agent_id: str | None = None

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

    def create_agent(self, name: str | None = None) -> AgentRecord:
        """Создаёт нового агента с дефолтными настройками и регистрирует его."""
        settings = AgentSettings.from_config(self.config)
        agent = Agent(
            name=name or f"chat-{len(self._records) + 1}",
            settings=settings,
            system_prompt=self._default_system_prompt,
            llm=self.llm,
            config=self.config,
            tools=self.tools,
            longterm=self.longterm,
        )
        agent_id = self.store.new_id()
        record = AgentRecord(
            agent_id=agent_id,
            agent=agent,
            session_id=self.store.new_id(),
            system_prompt_path=self._default_prompt_path,
        )
        self._records[agent_id] = record
        if self.active_agent_id is None:
            self.active_agent_id = agent_id
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
        agent = Agent(
            name=data.name or f"chat-{len(self._records) + 1}",
            settings=data.settings,
            system_prompt=data.system_prompt,
            llm=self.llm,
            config=self.config,
            tools=self.tools,
            longterm=self.longterm,
        )
        agent.apply_session(data)
        agent_id = self.store.new_id()
        record = AgentRecord(
            agent_id=agent_id,
            agent=agent,
            session_id=self.store.new_id(),
            system_prompt_path=self._default_prompt_path,
        )
        self._records[agent_id] = record
        self.active_agent_id = agent_id
        self.persist(record)
        return record

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
            active_branch=agent.memory.active_branch,
            branches=agent.memory.branches,
        )
        record.fingerprint = self._fingerprint(record)

    def persist_if_dirty(self) -> None:
        """Тик автосохранения: снапшот тех агентов, чей слепок изменился."""
        for record in self._records.values():
            if record.fingerprint != self._fingerprint(record):
                self.persist(record)

    # --- долговременная память (общая для всех агентов) ---

    def longterm_dto(self) -> LongTermDTO:
        """Текущее содержимое долговременной памяти (файл + записи)."""
        if self.longterm is None:
            return LongTermDTO(path="", content="", entries=[])
        return LongTermDTO(
            path=str(self.longterm.path),
            content=self.longterm.load(),
            entries=self.longterm.entries(),
        )

    def remember(self, text: str) -> LongTermDTO:
        """Добавляет знание в долговременную память; возвращает обновлённое состояние."""
        if self.longterm is None:
            raise RuntimeError("долговременная память недоступна")
        self.longterm.append(text)
        return self.longterm_dto()

    def forget(self, index: int) -> LongTermDTO:
        """Удаляет запись долговременной памяти по индексу."""
        if self.longterm is None:
            raise RuntimeError("долговременная память недоступна")
        self.longterm.remove(index)
        return self.longterm_dto()

    def update_longterm(self, index: int, text: str) -> LongTermDTO:
        """Заменяет запись долговременной памяти по индексу."""
        if self.longterm is None:
            raise RuntimeError("долговременная память недоступна")
        self.longterm.update(index, text)
        return self.longterm_dto()

    def accept_suggestion(self, agent_id: str) -> LongTermDTO:
        """Принять предложение агента: знание — в долговременную память."""
        record = self._records.get(agent_id)
        if record is None:
            raise KeyError("агент не найден")
        suggestion = record.agent.pending_memory_suggestion
        if suggestion is None:
            raise ValueError("нет предложения для сохранения")
        result = self.remember(suggestion)
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
            ),
            system_prompt=SystemPromptDTO(
                path=record.system_prompt_path,
                content=agent.system_prompt,
            ),
            context_used=context_now[0],
            context_window=agent.context_window,
            streaming=agent.is_streaming,
            compacting=agent.is_compacting or agent.is_extracting_facts,
            scratchpad=agent.memory.scratchpad,
            memory_suggestion=agent.pending_memory_suggestion,
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

    def sessions_dto(self, limit: int | None = None, offset: int = 0) -> list[SessionInfoDTO]:
        return [
            SessionInfoDTO(
                id=info.id,
                title=info.title,
                updated_at=info.updated_at,
                model=info.model,
                message_count=info.message_count,
            )
            for info in self.store.list(limit=limit, offset=offset)
        ]
