"""Класс Agent: инстанс на каждый чат.

Свои настройки (клон дефолтов config + runtime-override), свой системный
промпт, свой SessionMemory. Общие ресурсы (LLMClient, ToolRegistry, Config)
— передаются в конструктор и шарятся между агентами.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from my_agent.config.schema import AgentSettings, Config, Provider
from my_agent.core.compactor import CompactionResult, ContextCompactor
from my_agent.core.context import ContextBuilder, estimate_messages, estimate_text, fmt_tokens
from my_agent.core.message import (
    ChatChunk,
    ChatRequest,
    FunctionCall,
    Message,
    Role,
    ToolCall,
    Usage,
)
from my_agent.llm.client import LLMClient, LLMError
from my_agent.memory.session import InMemorySession, SessionData, save_session
from my_agent.tools.registry import ToolRegistry

CANCELLED_MARK = "… (запрос отменён)"

# серверный prompt_tokens ниже этой доли локальной оценки отправленного промпта —
# считаем, что провайдер обрезал контекст (модель не видела часть истории)
TRUNCATION_RATIO = 0.7


@dataclass
class TokenUsage:
    """Токены одного хода (для статус-бара).

    `estimated` — числа получены локальной оценкой (API не вернул usage).
    `truncated` — сервер обработал заметно меньше токенов, чем было
    отправлено: контекст модели переполнен, часть истории не видна.
    """

    prompt_tokens: int = 0  # полный контекст запроса: system + история
    completion_tokens: int = 0  # ответ модели
    estimated: bool = False
    truncated: bool = False


@dataclass
class SessionTotals:
    """Накопительный расход токенов за сессию (рантайм, в сессии не сохраняется).

    in — Σ prompt_tokens всех запросов (каждый запрос переотправляет контекст
    целиком — это честный расход); out — Σ completion_tokens ответов.
    Учитываются и LLM-вызовы сжатия контекста. `estimated` — хоть одна
    составляющая получена локальной оценкой (chars/4), а не от API.
    """

    in_tokens: int = 0
    out_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.in_tokens + self.out_tokens


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
    ) -> None:
        self.name = name
        self.settings = settings
        self.system_prompt = system_prompt
        self.memory = InMemorySession()
        self.context_builder = ContextBuilder()
        self._llm = llm
        self._config = config
        self._tools = tools or ToolRegistry()
        self._task: asyncio.Task[str] | None = None
        self._stream_text = ""
        self._stream_reasoning = ""  # размышления thinking-моделей (в API-проекцию не попадают)
        self._stream_tcs: dict[int, ToolCall] = {}
        self._finish_reason: str | None = None
        # --- сжатие контекста ---
        self._compactor = ContextCompactor(llm)
        self._needs_compaction = False  # прошлый ход обрезан сервером — сжать принудительно
        self.is_compacting = False  # сейчас идёт LLM-вызов суммаризации (читает UI)
        # наблюдаемое отношение «токены API / локальная оценка chars/4» (1.0–4.0):
        # локальная оценка занижает для русского — масштабируем ею компактор
        self._token_ratio: float | None = None
        # заметка о последнем сжатии (UI выводит после завершения хода)
        self.compaction_note: str | None = None
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
        в момент отправки). После хода = prompt + completion последнего
        запроса; во время стрима ответ добавляется живой оценкой; без
        единого хода — оценка проекции.
        """
        if self.last_usage is not None:
            if self.is_streaming:
                return self.last_usage.prompt_tokens + self.streaming_out_estimate, True
            return (
                self.last_usage.prompt_tokens + self.last_usage.completion_tokens,
                self.last_usage.estimated,
            )
        return estimate_messages(self._projection()), True

    def _projection(self) -> list[Message]:
        """Сообщения для LLM: system → саммари сжатого префикса → несжатый хвост.

        История агента хранится целиком (чат не меняется), в запрос уходит
        только проекция через ContextBuilder.
        """
        return self.context_builder.build_messages(
            self.system_prompt,
            self.memory.tail,
            summary=self.memory.summary,
        )

    def _projected_tokens(self) -> int:
        """Вес проекции следующего запроса к LLM.

        С точным usage API (статус-бар считает по нему же): prompt + completion
        последнего хода + оценка только что добавленного сообщения пользователя.
        Без точного usage — локальная оценка chars/4 всей проекции.
        """
        if self.last_usage is not None and not self.last_usage.estimated:
            new_user = estimate_messages(self.memory.tail[-1:]) if self.memory.tail else 0
            return self.last_usage.prompt_tokens + self.last_usage.completion_tokens + new_user
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

    async def ask(self, text: str) -> str:
        """Полный ход: user → LLM-стрим → assistant в истории. Возвращает ответ.

        LLMError пробрасывается (UI показывает в чате, чат продолжается).
        При отмене: частичный ответ сохраняется в истории, CancelledError — дальше.
        Токены хода: точные от API (usage в финальном чанке) либо оценка chars/4.
        Перед запросом: если контекст заполнил долю окна (compaction_threshold)
        либо прошлый ход был обрезан сервером — старейший префикс истории
        сжимается в саммари (чат при этом не меняется, меняется только
        проекция для LLM; заметка — в self.compaction_note).
        """
        self.compaction_note = None
        self.memory.add(Message(role=Role.USER, content=text))
        self._stream_text = ""
        self._stream_reasoning = ""
        self._stream_tcs = {}
        self._finish_reason = None
        self._server_usage = None
        previous_usage = self.last_usage
        messages: list[Message] = []
        try:
            provider, model = self._config.resolve_model(self.settings.model)
            await self._maybe_compact(provider, model)
            messages = self._projection()
            # живая оценка контекста — видна в статус-баре ещё до ответа API
            self.last_usage = TokenUsage(prompt_tokens=estimate_messages(messages), estimated=True)
            request = ChatRequest(
                model=model,
                messages=messages,
                temperature=self.settings.temperature,
                top_p=self.settings.top_p,
                max_tokens=self.settings.max_tokens,
                stop=self.settings.stop or None,
            )
            async for chunk in self._llm.astream(request, provider.api_base, provider.api_key):
                if chunk.usage is not None:
                    self._server_usage = chunk.usage
                if chunk.finish_reason:
                    self._finish_reason = chunk.finish_reason
                if chunk.content:
                    self._stream_text += chunk.content
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
            self.memory.add(
                Message(
                    role=Role.ASSISTANT,
                    content=self._stream_text or None,
                    reasoning=self._stream_reasoning or None,
                    tool_calls=self.streaming_tool_calls or None,
                )
            )
            self._finalize_usage(messages, assistant_added=True)
            return self._stream_text
        except asyncio.CancelledError:
            if self._stream_text:
                self.memory.add(
                    Message(
                        role=Role.ASSISTANT,
                        content=self._stream_text + CANCELLED_MARK,
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
                completion_tokens=self._server_usage.completion_tokens,
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
            self.last_usage = TokenUsage(
                prompt_tokens=estimate_messages(messages),
                completion_tokens=estimate_text(self._stream_text),
                estimated=True,
            )
        self.totals.in_tokens += self.last_usage.prompt_tokens
        self.totals.out_tokens += self.last_usage.completion_tokens
        if self.last_usage.estimated:
            self.totals.estimated = True
        if assistant_added:
            self.message_usage[len(self.memory.history) - 1] = self.last_usage

    async def _maybe_compact(self, provider: Provider, model: str) -> None:
        """Сжимает префикс истории в саммари, если контекст переполнен.

        Триггеры: заполнение доли окна (compaction_threshold) — по точному
        usage API, когда он есть (локальная оценка chars/4 занижает для
        русского, а статус-бар показывает точные числа), либо обрезка
        контекста сервером на прошлом ходу. Чат не меняется: сообщения
        остаются в истории, для LLM сжатый префикс заменяется саммари.
        Пользователь видит это по заметке compaction_note (UI выводит после хода).
        """
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
            f"⇄ Контекст сжат: -{fmt_tokens(result.removed_tokens)} удалено, "
            f"+{fmt_tokens(int(estimate_text(result.summary) * ratio))} саммари "
            f"({share:.0%} → {share_after:.0%})"
        )

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
        """Экспортирует сессию (полная история + саммари + настройки) в jsonl-файл."""
        return save_session(
            path,
            settings=self.settings,
            system_prompt=self.system_prompt,
            name=self.name,
            summary=self.memory.summary,
            compacted_upto=self.memory.compacted_upto,
            history=self.memory.history,
        )

    def apply_session(self, data: SessionData) -> None:
        if data.name:
            self.name = data.name
        self.settings = data.settings
        self.system_prompt = data.system_prompt
        self.memory.clear(summary=data.summary, compacted_upto=data.compacted_upto)
        for message in data.history:
            self.memory.add(message)
        self.last_usage = None
        self.message_usage = {}
        self.reset_totals()
        self._needs_compaction = False
        self._token_ratio = None
        self.compaction_note = None

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
