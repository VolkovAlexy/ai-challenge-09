"""Класс Agent: инстанс на каждый чат.

Свои настройки (клон дефолтов config + runtime-override), свой системный
промпт, свой SessionMemory. Общие ресурсы (LLMClient, ToolRegistry, Config)
— передаются в конструктор и шарятся между агентами.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent.config.schema import AgentSettings, Config, Provider
from agent.core.compactor import CompactionResult, ContextCompactor
from agent.core.context import (
    ContextBuilder,
    apply_sliding_window,
    estimate_messages,
    estimate_text,
    fmt_tokens,
)
from agent.core.message import (
    ChatChunk,
    ChatRequest,
    FunctionCall,
    Message,
    Role,
    ToolCall,
    Usage,
)
from agent.core.task import TaskPhase, TaskState
from agent.llm.client import LLMClient, LLMError
from agent.memory.facts import FactsExtractor
from agent.memory.longterm import LongTermSource
from agent.memory.session import InMemorySession, SessionData, save_session
from agent.tools.context import ToolContext
from agent.tools.invariants import invariant_tools
from agent.tools.registry import Tool, ToolRegistry
from agent.tools.scratchpad import parse_tool_arguments, scratchpad_tools
from agent.tools.task import task_tools

CANCELLED_MARK = "… (запрос отменён)"

# серверный prompt_tokens ниже этой доли локальной оценки отправленного промпта —
# считаем, что провайдер обрезал контекст (модель не видела часть истории)
TRUNCATION_RATIO = 0.7

# максимум tool-раундов на ход; последний LLM-вызов идёт без инструментов,
# чтобы модель завершала ход текстом, а не «висящим» tool_call без результата
MAX_TOOL_ROUNDS = 5

# автопилот: после подтверждения плана агент сам гонит задачу до фазы «готово»
# (выполнение → проверка → готово), подсказывая себе продолжение, пока фаза
# меняется или продвигается шаг. Каждый «под-ход» ограничен MAX_TOOL_ROUNDS.
AUTOPILOT_MAX_TURNS = 15
# префикс внутреннего «продолжай», который фронт скрывает из чата
AUTOPILOT_MARKER = "[АВТОПРОДОЛЖЕНИЕ] "

# предложение агента сохранить знание в долговременную память (вырезается из ответа)
MEMORY_SUGGESTION_RE = re.compile(
    r"\[MEMORY_SUGGESTION\](.*?)\[/MEMORY_SUGGESTION\]", re.DOTALL
)


@dataclass
class TokenUsage:
    """Токены одного хода (для статус-бара).

    `estimated` — числа получены локальной оценкой (API не вернул usage).
    `truncated` — сервер обработал заметно меньше токенов, чем было
    отправлено: контекст модели переполнен, часть истории не видна.
    """

    prompt_tokens: int = 0  # полный контекст запроса: system + история
    completion_tokens: int = 0  # весь вывод модели, включая размышления
    reasoning_tokens: int = 0  # из completion_tokens: размышления (think)
    estimated: bool = False
    truncated: bool = False

    @property
    def answer_tokens(self) -> int:
        """Видимый ответ без размышлений — только он остаётся в контексте."""
        return self.completion_tokens - self.reasoning_tokens


@dataclass
class SessionTotals:
    """Накопительный расход токенов за сессию (рантайм, в сессии не сохраняется).

    in — Σ prompt_tokens всех запросов (каждый запрос переотправляет контекст
    целиком — это честный расход); out — Σ completion_tokens ответов, включая
    размышления thinking-моделей.
    Учитываются и LLM-вызовы сжатия контекста. `estimated` — хоть одна
    составляющая получена локальной оценкой (chars/4), а не от API.
    """

    in_tokens: int = 0
    out_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens


@dataclass
class SubagentEvent:
    """Событие работы субагента (дренируется SSE-стримером в UI).

    `kind` — "started" | "delta" | "done"; `profile` — имя роли субагента.
    """

    kind: str
    profile: str
    content: str = ""


class AgentBusyError(Exception):
    """У агента уже есть активный запрос."""


class Agent:
    def __init__(
        self,
        *,
        name: str,
        settings: AgentSettings,
        system_prompt: str,
        llm: LLMClient,
        config: Config,
        tools: ToolRegistry | None = None,
        longterm: LongTermSource | None = None,
        project_id: str = "",
        active_profile_id: str = "",
        with_task_tools: bool = True,
    ) -> None:
        self.name = name
        self.settings = settings
        self.system_prompt = system_prompt
        self.project_id = project_id
        self.active_profile_id = active_profile_id  # профиль роли чата ("" — без него)
        self.profile_content = ""  # текст активного профиля (подтягивается из store)
        self.memory = InMemorySession()
        self.context_builder = ContextBuilder()
        self._llm = llm
        self._config = config
        # per-agent реестр: общие инструменты + инструменты рабочей памяти,
        # привязанные к сессии этого агента (scratchpad у каждого свой)
        self._tools = ToolRegistry()
        for tool in (tools or ToolRegistry()).all():
            self._tools.register(tool)
        for tool in scratchpad_tools(self.memory):
            self._tools.register(tool)
        # субагенты (делегирование) не используют конечный автомат задачи:
        # они — чистые исполнители, получают задачу+контекст от оркестратора
        # и не планируют/не запрашивают подтверждений (with_task_tools=False).
        if with_task_tools:
            for tool in task_tools(self.memory):
                self._tools.register(tool)
            for tool in invariant_tools(self.memory):
                self._tools.register(tool)
        # долговременная память: уровень проекта (своя у каждого проекта)
        self._longterm = longterm
        self._task: asyncio.Task[str] | None = None
        self._stream_text = ""
        self._stream_reasoning = ""  # размышления thinking-моделей (в API-проекцию не попадают)
        self._stream_tcs: dict[int, ToolCall] = {}
        self._finish_reason: str | None = None
        # --- сжатие контекста ---
        self._compactor = ContextCompactor(llm)
        self._needs_compaction = False  # прошлый ход обрезан сервером — сжать принудительно
        self.is_compacting = False  # сейчас идёт LLM-вызов суммаризации (читает UI)
        self.is_extracting_facts = False  # сейчас идёт LLM-вызов обновления facts (читает UI)
        # наблюдаемое отношение «токены API / локальная оценка chars/4» (1.0–4.0):
        # локальная оценка занижает для русского — масштабируем ею компактор
        self._token_ratio: float | None = None
        # заметка о последнем сжатии (текст совпадает с тем, что уходит в колбэк)
        self.compaction_note: str | None = None
        # подписка UI: вызывается сразу после успешного сжатия, до ответа модели
        self.on_compaction: Callable[[str], None] | None = None
        # --- facts (стратегия facts) ---
        self._facts_extractor = FactsExtractor(llm)
        self.facts_note: str | None = None
        # подписка UI: вызывается сразу после обновления facts, до ответа модели
        self.on_facts: Callable[[str], None] | None = None
        # --- долговременная память: предложение, ждущее решения пользователя ---
        self._pending_memory_suggestion: str | None = None
        # --- tool-раунды текущего хода (usage промежуточных LLM-вызовов) ---
        self._round_usages: list[Usage] = []
        # артефакты tool-раундов текущего хода (индекс в истории, сообщение):
        # дренирует SSE-стример (web), чтобы фронт видел вызовы инструментов
        self.turn_events: list[tuple[int, Message]] = []
        # события субагентов текущего хода (оркестратор делегирует дрену SSE-стримеру)
        self.subagent_events: list[SubagentEvent] = []
        # --- токены (только рантайм, в сессии не сохраняются) ---
        self.last_usage: TokenUsage | None = None
        # индекс assistant-сообщения в истории → токены его хода
        self.message_usage: dict[int, TokenUsage] = {}
        self._server_usage: Usage | None = None
        # накопительный расход за сессию (in/out/Σ), включая вызовы компакции
        self.totals = SessionTotals()

    # --- состояние стриминга (читает UI) ---

    @property
    def is_streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def is_thinking(self) -> bool:
        """Стрим идёт, но контента ещё нет — время показывать лоадер."""
        return self.is_streaming and not self._stream_text and not self._stream_tcs

    @property
    def streaming_text(self) -> str:
        """Частичный вывод текущего запроса (для catch-up при переключении вкладок)."""
        return self._stream_text

    @property
    def streaming_reasoning(self) -> str:
        """Частичные размышления текущего запроса (для live-индикации «думаю…»)."""
        return self._stream_reasoning

    @property
    def streaming_tool_calls(self) -> list[ToolCall]:
        return list(self._stream_tcs.values())

    @property
    def streaming_out_estimate(self) -> int:
        """Живая оценка токенов текущего ответа во время стрима (chars/4)."""
        return estimate_text(self._stream_text)

    @property
    def has_first_response(self) -> bool:
        """Появился ли уже первый ответ модели: в истории или стримится сейчас.

        До него контекст неизвестен (неясно, что введёт пользователь),
        поэтому UI скрывает индикатор context.
        """
        return bool(self._stream_text or self._stream_tcs) or any(
            m.role is Role.ASSISTANT for m in self.memory.history
        )

    @property
    def context_window(self) -> int:
        """Размер контекстного окна текущей модели (из config, с дефолтом)."""
        try:
            return self._config.context_window_for(self.settings.model)
        except ValueError:
            return self._config.context_window_default

    @property
    def compaction_threshold(self) -> float:
        """Доля заполнения окна, при которой сжимается история (из config)."""
        return self._config.compaction_threshold

    @property
    def context_now(self) -> tuple[int, bool]:
        """Текущий вес контекста: то, что уйдёт в LLM со следующим промптом.

        system + саммари + несжатая история (+ новый пользовательский текст
        в момент отправки). После хода = prompt + answer (видимый ответ;
        размышления в API-проекцию не уходят); во время стрима ответ
        добавляется живой оценкой; без единого хода — оценка проекции.
        """
        if self.last_usage is not None:
            if self.is_streaming:
                return self.last_usage.prompt_tokens + self.streaming_out_estimate, True
            return (
                self.last_usage.prompt_tokens + self.last_usage.answer_tokens,
                self.last_usage.estimated,
            )
        return estimate_messages(self._projection()), True

    @property
    def effective_system_prompt(self) -> str:
        """Системный промпт для LLM: базовый + текст активного профиля (склеивание)."""
        if self.profile_content:
            return f"{self.system_prompt}\n\n{self.profile_content}"
        return self.system_prompt

    def set_active_profile(self, profile_id: str, content: str) -> None:
        """Назначает активный профиль роли (content — текст профиля, "" — снять его)."""
        self.active_profile_id = profile_id
        self.profile_content = content

    def _projection(self) -> list[Message]:
        """Сообщения для LLM — согласно стратегии контекста.

        none — вся история как есть; summary — саммари сжатого префикса +
        несжатый хвост; sliding — последние N сообщений; facts — facts-блок +
        последние N сообщений. История агента хранится целиком (чат не
        меняется), в запрос уходит только проекция через ContextBuilder.
        Поверх системного промпта добавляются долговременная память
        (уровень проекта, своя у каждого проекта) и рабочая память
        текущей задачи (scratchpad).
        """
        longterm_raw = self._longterm.load() if self._longterm is not None else ""
        longterm = longterm_raw.strip() or None
        scratchpad = self.memory.scratchpad.strip() or None
        invariants = self.memory.invariants or None
        task = self.memory.task.state
        strategy = self.settings.context_strategy
        if strategy == "none":
            return self.context_builder.build_messages(
                self.effective_system_prompt,
                self.memory.history,
                longterm=longterm,
                scratchpad=scratchpad,
                invariants=invariants,
                task=task,
            )
        if strategy == "summary":
            return self.context_builder.build_messages(
                self.effective_system_prompt,
                self.memory.tail,
                summary=self.memory.summary,
                longterm=longterm,
                scratchpad=scratchpad,
                invariants=invariants,
                task=task,
            )
        # sliding / facts: скользящее окно по полной истории
        return self.context_builder.build_messages(
            self.effective_system_prompt,
            apply_sliding_window(self.memory.history, self.settings.sliding_window),
            facts=self.memory.facts or None if strategy == "facts" else None,
            longterm=longterm,
            scratchpad=scratchpad,
            invariants=invariants,
            task=task,
        )

    def _projected_tokens(self) -> int:
        """Вес проекции следующего запроса к LLM.

        С точным usage API (статус-бар считает по нему же): prompt + видимый
        ответ последнего хода (размышления в проекцию не уходят) + оценка
        только что добавленного сообщения пользователя.
        Без точного usage — локальная оценка chars/4 всей проекции.
        """
        if self.last_usage is not None and not self.last_usage.estimated:
            new_user = estimate_messages(self.memory.tail[-1:]) if self.memory.tail else 0
            return self.last_usage.prompt_tokens + self.last_usage.answer_tokens + new_user
        return estimate_messages(self._projection())

    @property
    def context_share(self) -> float | None:
        """Заполнение окна (0.0–1.0) или None, если контекст неизвестен."""
        if not self.has_first_response:
            return None
        return self.context_now[0] / self.context_window

    @property
    def settings_dirty(self) -> bool:
        """Настройки агента отличаются от глобальных дефолтов config.json."""
        return self.settings != AgentSettings.from_config(self._config)

    # --- запросы ---

    def start_ask(self, text: str) -> asyncio.Task[str]:
        """Запускает ask() как задачу. Один активный запрос на агента."""
        if self.is_streaming:
            raise AgentBusyError(f"агент '{self.name}' уже отвечает")
        self._task = asyncio.create_task(self.ask(text), name=f"ask:{self.name}")
        return self._task

    def cancel_ask(self) -> bool:
        """Отменяет активный запрос. True, если что-то отменилось."""
        if self.is_streaming and self._task is not None:
            self._task.cancel()
            return True
        return False

    async def ask(
        self, text: str, *, on_delta: Callable[[str], None] | None = None
    ) -> str:
        """Полный ход: user → LLM-стрим → assistant в истории. Возвращает ответ.

        `on_delta` — колбэк на каждый чанк контента (используется делегированием:
        оркестратор пробрасывает ответ субагента в свой subagent_events).

        LLMError пробрасывается (UI показывает в чате, чат продолжается).
        При отмене: частичный ответ сохраняется в истории, CancelledError — дальше.
        Токены хода: точные от API (usage в финальном чанке) либо оценка chars/4.
        Перед запросом: если контекст заполнил долю окна (compaction_threshold)
        либо прошлый ход был обрезан сервером — старейший префикс истории
        сжимается в саммари (чат при этом не меняется, меняется только
        проекция для LLM; заметка — в self.compaction_note и колбэку
        on_compaction (UI показывает её до ответа модели).
        Инструменты: если модель вызвала tool_calls, они исполняются
        (реестр self._tools), результаты уходят в историю роли tool, запрос
        повторяется — не более MAX_TOOL_ROUNDS раундов на ход.
        Блок [MEMORY_SUGGESTION] в ответе вырезается: текст знания ждёт
        решения пользователя (self._pending_memory_suggestion).
        Автопилот: после подтверждения плана агент сам гонит задачу до фазы
        «готово», подсказывая себе продолжение (`AUTOPILOT_MARKER` и
        `AUTOPILOT_MAX_TURNS`), пока фаза/шаг продвигаются; пользователь ничего
        не вводит до фазы «готово».
        """
        self.compaction_note = None
        self.facts_note = None
        self._pending_memory_suggestion = None
        self.turn_events = []
        self.subagent_events = []
        self.memory.add(Message(role=Role.USER, content=text))
        self._stream_text = ""
        self._stream_reasoning = ""
        self._stream_tcs = {}
        self._finish_reason = None
        self._server_usage = None
        self._round_usages = []
        previous_usage = self.last_usage
        messages: list[Message] = []
        try:
            provider, model = self._config.resolve_model(self.settings.model)
            if self.settings.context_strategy == "facts":
                await self._update_facts(provider, model)
            await self._maybe_compact(provider, model)
            api_tools = self._tools.to_api_tools() or None
            api_tools = self._tools.to_api_tools() or None
            prev_sig = self._task_signature()
            answer = ""
            for _autopilot_turn in range(AUTOPILOT_MAX_TURNS + 1):
                for round_no in range(MAX_TOOL_ROUNDS + 1):
                    messages = self._projection()
                    # живая оценка контекста — видна в статус-баре ещё до ответа API
                    self.last_usage = TokenUsage(
                        prompt_tokens=estimate_messages(messages), estimated=True
                    )
                    # На последнем раунде инструменты не предлагаем: модель обязана
                    # завершить ход текстом, а не оставить «висящий» tool_call без
                    # результата (иначе ход обрывался и требовал ручного «продолжай»).
                    tools = api_tools if round_no < MAX_TOOL_ROUNDS else None
                    request = ChatRequest(
                        model=model,
                        messages=messages,
                        temperature=self.settings.temperature,
                        top_p=self.settings.top_p,
                        max_tokens=self.settings.max_tokens,
                        stop=self.settings.stop or None,
                        tools=tools,
                    )
                    async for chunk in self._llm.astream(
                        request, provider.api_base, provider.api_key
                    ):
                        if chunk.usage is not None:
                            self._server_usage = chunk.usage
                            self._round_usages.append(chunk.usage)
                        if chunk.finish_reason:
                            self._finish_reason = chunk.finish_reason
                        if chunk.content:
                            self._stream_text += chunk.content
                            if on_delta is not None:
                                on_delta(chunk.content)
                        if chunk.reasoning:
                            self._stream_reasoning += chunk.reasoning
                        self._accumulate_tool_calls(chunk)
                    if not self._stream_text and not self._stream_tcs:
                        # пустой ответ не сохраняем: он бесполезен в истории и отравил бы
                        # проекцию следующего запроса. Типичный случай — thinking-модель
                        # израсходовала max_tokens размышлениями (delta.reasoning) и не
                        # начала видимый ответ (finish_reason=length).
                        if self._finish_reason == "length":
                            raise LLMError(
                                "модель исчерпала max_tokens на размышления и не начала ответ — "
                                "увеличьте лимит: /max-tokens <n>"
                            )
                        raise LLMError(
                            f"модель вернула пустой ответ (finish_reason: {self._finish_reason})"
                        )
                    if not self._stream_tcs or round_no >= MAX_TOOL_ROUNDS:
                        break
                    # раунд инструментов: результаты в историю, затем новый запрос
                    assistant_msg = Message(
                        role=Role.ASSISTANT,
                        content=self._stream_text or None,
                        reasoning=self._stream_reasoning or None,
                        tool_calls=self.streaming_tool_calls or None,
                    )
                    self.memory.add(assistant_msg)
                    self.turn_events.append((len(self.memory.history) - 1, assistant_msg))
                    await self._execute_tool_calls(self.streaming_tool_calls)
                    self._stream_text = ""
                    self._stream_reasoning = ""
                    self._stream_tcs = {}
                    self._finish_reason = None
                answer = self._extract_memory_suggestion(self._stream_text)
                assistant_msg = Message(
                    role=Role.ASSISTANT,
                    content=answer or None,
                    reasoning=self._stream_reasoning or None,
                    tool_calls=self.streaming_tool_calls or None,
                )
                self.memory.add(assistant_msg)
                # автопилот: пока задача в выполнении/проверке и шаг/фаза продвинулись,
                # подсказываем модели продолжение сами — пользователь ничего не вводит
                # до фазы «готово».
                if not self._autopilot_continue(prev_sig):
                    break
                prev_sig = self._task_signature()
                # промежуточный ответ показываем в чате отдельным сообщением
                self.turn_events.append((len(self.memory.history) - 1, assistant_msg))
                self.memory.add(
                    Message(role=Role.USER, content=self._autopilot_prompt())
                )
                self._stream_text = ""
                self._stream_reasoning = ""
                self._stream_tcs = {}
                self._finish_reason = None
            self._finalize_usage(messages, assistant_added=True)
            return answer
        except asyncio.CancelledError:
            if self._stream_text:
                answer = self._extract_memory_suggestion(self._stream_text)
                self.memory.add(
                    Message(
                        role=Role.ASSISTANT,
                        content=answer + CANCELLED_MARK,
                        reasoning=self._stream_reasoning or None,
                    )
                )
            self._finalize_usage(messages, assistant_added=bool(self._stream_text))
            raise
        except Exception:
            self.last_usage = previous_usage  # ход не состоялся — показываем прошлый
            raise
        finally:
            self._stream_text = ""
            self._stream_reasoning = ""
            self._stream_tcs = {}
            self._finish_reason = None
            self._round_usages = []

    def _extract_memory_suggestion(self, text: str) -> str:
        """Вырезает [MEMORY_SUGGESTION]-блок из ответа; текст ждёт решения UI.

        Возвращает ответ без блока (в историю и чат маркер не попадает).
        """
        match = MEMORY_SUGGESTION_RE.search(text)
        if match is None:
            return text
        suggestion = match.group(1).strip()
        if suggestion:
            self._pending_memory_suggestion = suggestion
        cleaned = MEMORY_SUGGESTION_RE.sub("", text).strip()
        return cleaned

    def _task_signature(self) -> tuple[str, int] | None:
        """Сигнатура задачи (фаза, шаг) для детекта прогресса автопилота."""
        state = self.memory.task.state
        if not state.is_active:
            return None
        return (state.phase.value, state.step)

    def _autopilot_continue(self, prev_sig: tuple[str, int] | None) -> bool:
        """Продолжать ли автопилот: задача в выполнении/проверке, и шаг/фаза продвинулись.

        Если фаза не изменилась и шаг не сдвинулся — модель «выдохлась» и ход
        завершается, чтобы не зациклиться (пользователь тогда продолжит сам).
        """
        state = self.memory.task.state
        if not state.is_active or state.paused:
            return False
        if state.phase not in (TaskPhase.EXECUTION, TaskPhase.VALIDATION):
            return False
        current = self._task_signature()
        return not (prev_sig is not None and current == prev_sig)

    def _autopilot_prompt(self) -> str:
        """Внутренняя подсказка модели продолжить задачу (скрывается из чата)."""
        state = self.memory.task.state
        if state.phase is TaskPhase.VALIDATION:
            return (
                f"{AUTOPILOT_MARKER}Продолжи проверку автоматически: проверь результат "
                "по «Ограничениям». Если всё в порядке — заверши: set_phase(done). "
                "Если есть правки — вернись в выполнение: set_phase(execution). "
                "Если план нужно скорректировать — вернись в планирование: "
                "set_phase(planning). Не останавливайся до фазы «готово»."
            )
        return (
            f"{AUTOPILOT_MARKER}Продолжи выполнение плана автоматически: выполни "
            "текущий шаг (при необходимости делегируй субагенту через delegate), "
            "продвигай шаги (task_advance_step). Не останавливайся до фазы «готово»."
        )

    @property
    def pending_memory_suggestion(self) -> str | None:
        """Предложение сохранить знание в долговременную память (читает UI)."""
        return self._pending_memory_suggestion

    def dismiss_suggestion(self) -> None:
        """Закрывает предложение памяти (принято или отклонено — решает UI)."""
        self._pending_memory_suggestion = None

    async def _execute_tool_calls(self, calls: list[ToolCall]) -> None:
        """Исполняет tool_calls последнего ответа, результаты — в историю (роль tool)."""
        for call in calls:
            tool_msg = await self._execute_tool_call(call)
            self.memory.add(tool_msg)
            self.turn_events.append((len(self.memory.history) - 1, tool_msg))

    async def _execute_tool_call(self, call: ToolCall) -> Message:
        """Исполняет один tool_call; ошибка инструмента возвращается моделью как текст."""
        tool = self._tools.get(call.function.name)
        if tool is None:
            output = f"инструмент '{call.function.name}' не найден"
        else:
            ctx = ToolContext(
                session=self.memory, agent=self, project_id=self.project_id, run_id=uuid4().hex
            )
            try:
                arguments = parse_tool_arguments(call.function.arguments)
                result = await tool.execute(arguments, ctx)
                output = result.output
            except ValueError as exc:
                output = f"неверные аргументы: {exc}"
            except Exception as exc:  # падение инструмента не роняет ход
                output = f"ошибка инструмента: {exc}"
        return Message(
            role=Role.TOOL, content=output, tool_call_id=call.id, name=call.function.name
        )

    def register_tools(self, tools: Sequence[Tool]) -> None:
        """Регистрирует инструменты в per-agent реестре (например, делегирование).

        Вызов безопасен в любой момент: инструменты добавляются к уже
        зарегистрированным; повторное имя перезаписывается.
        """
        for tool in tools:
            self._tools.register(tool)

    def sync_dynamic_tools(self, tools: Sequence[Tool]) -> None:
        """Заменяет набор динамических (внешних, MCP) инструментов агента.

        Убирает все ранее зарегистрированные динамические инструменты и
        регистрирует переданные заново. Статические инструменты агента
        (рабочая память, задача, делегирование) не затрагиваются.
        """
        for name in list(self._tools._dynamic):
            self._tools.unregister(name)
        for tool in tools:
            self._tools.register_dynamic(tool)

    # --- события субагентов (оркестратор → SSE-стример) ---

    def begin_subagent(self, profile: str) -> None:
        self.subagent_events.append(SubagentEvent(kind="started", profile=profile))

    def stream_subagent(self, profile: str, content: str) -> None:
        self.subagent_events.append(SubagentEvent(kind="delta", profile=profile, content=content))

    def end_subagent(self, profile: str) -> None:
        self.subagent_events.append(SubagentEvent(kind="done", profile=profile))

    def _finalize_usage(self, messages: list[Message], *, assistant_added: bool) -> None:
        """Фиксирует токены завершившегося хода и привязывает их к ответу ассистента.

        Здесь же ход попадает в накопительные счётчики сессии (in/out/Σ);
        неудавшийся ход (LLMError до финализации) сюда не доходит и не учитывается.
        """
        if self._server_usage is not None:
            sent_estimate = estimate_messages(messages)
            truncated = self._server_usage.prompt_tokens < sent_estimate * TRUNCATION_RATIO
            self.last_usage = TokenUsage(
                prompt_tokens=self._server_usage.prompt_tokens,
                # completion_tokens у API уже включает размышления; reasoning
                # выделяем отдельно (для строки think под репликой и оценок)
                completion_tokens=self._server_usage.completion_tokens,
                reasoning_tokens=self._server_usage.reasoning_tokens,
                estimated=False,
                truncated=truncated,
            )
            if truncated:
                self._needs_compaction = True  # сжать при следующем ходе
            if sent_estimate > 0:  # коэффициент токенизатора: API / chars/4
                self._token_ratio = min(
                    4.0, max(1.0, self._server_usage.prompt_tokens / sent_estimate)
                )
        else:
            # API не вернул usage: reasoning-токены оцениваем тоже — thinking-
            # модель может израсходовать на размышления львиную долю вывода
            reasoning = estimate_text(self._stream_reasoning)
            self.last_usage = TokenUsage(
                prompt_tokens=estimate_messages(messages),
                completion_tokens=estimate_text(self._stream_text) + reasoning,
                reasoning_tokens=reasoning,
                estimated=True,
            )
        self.totals.in_tokens += self.last_usage.prompt_tokens
        self.totals.out_tokens += self.last_usage.completion_tokens
        if self.last_usage.estimated:
            self.totals.estimated = True
        # расход промежуточных tool-раундов (все вызовы LLM хода, кроме последнего)
        for usage in self._round_usages[:-1]:
            self.totals.in_tokens += usage.prompt_tokens
            self.totals.out_tokens += usage.completion_tokens
        if assistant_added:
            self.message_usage[len(self.memory.history) - 1] = self.last_usage

    async def _maybe_compact(self, provider: Provider, model: str) -> None:
        """Сжимает префикс истории в саммари, если контекст переполнен.

        Работает только в стратегии summary (в sliding/facts окно само
        ограничивает проекцию, в none — сжатия нет вовсе). Триггеры:
        заполнение доли окна (compaction_threshold) — по точному usage API,
        когда он есть (локальная оценка chars/4 занижает для русского, а
        статус-бар показывает точные числа), либо обрезка контекста сервером
        на прошлом ходу. Чат не меняется: сообщения остаются в истории, для
        LLM сжатый префикс заменяется саммари. Пользователь видит это по
        заметке compaction_note (UI выводит сразу после сжатия, до ответа
        модели, через колбэк on_compaction).
        """
        if self.settings.context_strategy != "summary":
            self._needs_compaction = False
            return
        window = self.context_window
        threshold = self._config.compaction_threshold
        share = self._projected_tokens() / window if window > 0 else 1.0
        if share < threshold and not self._needs_compaction:
            return
        ratio = self._token_ratio or 1.0
        result: CompactionResult | None = None
        self.is_compacting = True
        try:
            result = await self._compactor.compact(
                model=model,
                api_base=provider.api_base,
                api_key=provider.api_key,
                previous_summary=self.memory.summary or "",
                history=self.memory.tail,
                window=window,
                system_prompt=self.system_prompt,
                ratio=ratio,
            )
        finally:
            self.is_compacting = False
        if result is None:
            self._needs_compaction = False
            return
        self.memory.compact_prefix(result.removed, summary=result.summary)
        # расход LLM-вызова суммаризации — тоже траты сессии
        self.totals.in_tokens += result.in_tokens
        self.totals.out_tokens += result.out_tokens
        if result.estimated:
            self.totals.estimated = True
        self.last_usage = None  # прошлый замер больше не соответствует проекции
        self._needs_compaction = False
        share_after = (
            estimate_messages(self._projection()) * ratio / window if window > 0 else 1.0
        )
        self.compaction_note = (
            f"Контекст сжат: -{fmt_tokens(result.removed_tokens)} удалено, "
            f"+{fmt_tokens(int(estimate_text(result.summary) * ratio))} саммари "
            f"({share:.0%} → {share_after:.0%})"
        )
        if self.on_compaction is not None:
            # UI вставляет заметку в таймлайн сейчас: между запросом пользователя
            # и будущим ответом. Даже если ход после сжатия упадёт — она уже видна.
            self.on_compaction(self.compaction_note)

    async def _update_facts(self, provider: Provider, model: str) -> None:
        """Обновляет facts после сообщения пользователя (стратегия facts).

        Один LLM-вызов: текущие facts + недавний хвост диалога → обновлённый
        JSON. При ошибке старые facts сохраняются, чат продолжается (заметка
        — в self.facts_note и колбэку on_facts). Расход вызова попадает в
        накопительные счётчики сессии.
        """
        self.is_extracting_facts = True
        try:
            result = await self._facts_extractor.update(
                model=model,
                api_base=provider.api_base,
                api_key=provider.api_key,
                facts=self.memory.facts,
                history=self.memory.tail,
            )
        except LLMError as exc:
            self.facts_note = f"Не удалось обновить facts: {exc}"
            if self.on_facts is not None:
                self.on_facts(self.facts_note)
            return
        finally:
            self.is_extracting_facts = False
        changed = result.facts != self.memory.facts
        self.memory.facts = result.facts
        self.totals.in_tokens += result.in_tokens
        self.totals.out_tokens += result.out_tokens
        if result.estimated:
            self.totals.estimated = True
        if changed:
            self.facts_note = (
                "Facts обновлены: " + ", ".join(sorted(result.facts))
                if result.facts
                else "Facts очищены."
            )
            if self.on_facts is not None:
                self.on_facts(self.facts_note)

    def _accumulate_tool_calls(self, chunk: ChatChunk) -> None:
        """Накопительный разбор tool_calls в стриме (дальше — задел под ToolRegistry)."""
        for delta in chunk.tool_call_deltas:
            slot = self._stream_tcs.setdefault(
                delta.index,
                ToolCall(id="", function=FunctionCall(name="", arguments="")),
            )
            if delta.id:
                slot.id = delta.id
            if delta.function_name:
                slot.function.name = delta.function_name
            if delta.function_arguments:
                slot.function.arguments += delta.function_arguments

    # --- сессии ---

    def export(self, path: str | Path) -> Path:
        """Экспортирует сессию (все ветки + facts + настройки) в jsonl-файл."""
        return save_session(
            path,
            settings=self.settings,
            system_prompt=self.system_prompt,
            name=self.name,
            summary=self.memory.summary,
            compacted_upto=self.memory.compacted_upto,
            history=self.memory.history,
            facts=self.memory.facts,
            invariants=self.memory.invariants,
            task=self.memory.task.state,
            active_branch=self.memory.active_branch,
            branches=self.memory.branches,
            active_profile_id=self.active_profile_id,
        )

    def apply_session(self, data: SessionData) -> None:
        if data.name:
            self.name = data.name
        self.settings = data.settings
        self.system_prompt = data.system_prompt
        self.memory.clear(summary=data.summary, compacted_upto=data.compacted_upto)
        for message in data.history:
            self.memory.add(message)
        self.memory.facts = dict(data.facts)
        self.memory.scratchpad = data.scratchpad
        self.memory.invariants = list(data.invariants)
        self.memory.task.state = data.task if data.task is not None else TaskState()
        self.memory.restore_branches(data.branches, active=data.active_branch)
        self.active_profile_id = data.active_profile_id
        self.profile_content = ""  # текст подтягивается веб-слоем из store
        self._reset_runtime()

    # --- ветки диалога ---

    def fork_branch(self, name: str) -> str:
        """Создаёт ветку-копию текущего диалога и переключается на неё.

        Возвращает имя прежней активной ветки. ValueError — имя занято;
        вызывать только вне активного запроса.
        """
        previous = self.memory.active_branch
        self.memory.fork(name)
        self._reset_runtime()
        return previous

    def fork_at(self, name: str, index: int) -> str:
        """Ветка от сообщения: копия истории до index включительно + переключение.

        Возвращает имя прежней активной ветки. ValueError — имя занято,
        IndexError — index вне истории. Вызывать только вне активного запроса.
        """
        if index < 0 or index >= len(self.memory.history):
            raise IndexError(f"индекс сообщения вне истории: {index}")
        previous = self.fork_branch(name)
        self.memory.truncate_to(index + 1)
        return previous

    def switch_branch(self, name: str) -> None:
        """Переключается на сохранённую ветку. KeyError — ветки нет."""
        self.memory.switch(name)
        self._reset_runtime()

    def _reset_runtime(self) -> None:
        """Сброс рантайм-состояния после смены состояния диалога (ветка/сессия)."""
        self.last_usage = None
        self.message_usage = {}
        self.reset_totals()
        self._needs_compaction = False
        self._token_ratio = None
        self.compaction_note = None
        self.facts_note = None
        self._pending_memory_suggestion = None
        self.subagent_events = []

    def request_compaction(self) -> None:
        """Форсирует сжатие префикса истории при следующем ходе (команда /compact)."""
        self._needs_compaction = True

    def reset_totals(self) -> None:
        """Обнуляет накопительные счётчики токенов сессии (in/out/Σ)."""
        self.totals = SessionTotals()

    # --- настройка через команды (runtime-override) ---

    def set_model(self, model_id: str) -> None:
        """Валидирует id модели против config и применяет к агенту."""
        self._config.resolve_model(model_id)  # ValueError при неизвестной
        self.settings.model = model_id
        self._token_ratio = None  # коэффициент токенизатора — свой у каждой модели

    def rename(self, name: str) -> None:
        self.name = name

    def set_system_prompt_file(self, path: str) -> None:
        """Заменяет системный промпт содержимым файла (FileNotFoundError пробрасывается)."""
        self.system_prompt = Path(path).read_text(encoding="utf-8")

    def set_project(self, project_id: str, longterm: LongTermSource | None) -> None:
        """Перевязывает агента на другой проект (меняется его долговременная память)."""
        self.project_id = project_id
        self._longterm = longterm
