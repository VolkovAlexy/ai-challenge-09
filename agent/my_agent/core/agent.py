"""Класс Agent: инстанс на каждый чат.

Свои настройки (клон дефолтов config + runtime-override), свой системный
промпт, свой SessionMemory. Общие ресурсы (LLMClient, ToolRegistry, Config)
— передаются в конструктор и шарятся между агентами.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from my_agent.config.schema import AgentSettings, Config
from my_agent.core.context import ContextBuilder
from my_agent.core.message import (
    ChatChunk,
    ChatRequest,
    FunctionCall,
    Message,
    Role,
    ToolCall,
    Usage,
)
from my_agent.llm.client import LLMClient
from my_agent.memory.session import InMemorySession, SessionData, save_session
from my_agent.tools.registry import ToolRegistry

CANCELLED_MARK = "… (запрос отменён)"

CHARS_PER_TOKEN = 4  # грубая оценка для фолбэка, когда API не вернул usage

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
        self._stream_tcs: dict[int, ToolCall] = {}
        # --- токены (только рантайм, в сессии не сохраняются) ---
        self.last_usage: TokenUsage | None = None
        # индекс assistant-сообщения в истории → токены его хода
        self.message_usage: dict[int, TokenUsage] = {}
        self._server_usage: Usage | None = None

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
    def streaming_tool_calls(self) -> list[ToolCall]:
        return list(self._stream_tcs.values())

    @property
    def streaming_out_estimate(self) -> int:
        """Живая оценка токенов текущего ответа во время стрима (chars/4)."""
        return self._estimate_text(self._stream_text)

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
    def context_now(self) -> tuple[int, bool]:
        """Текущий вес контекста: system + вся история прямо сейчас.

        Возвращает (токены, estimated). После хода = prompt + completion
        последнего запроса (то, что уйдёт при следующем сообщении, минус
        токены самого нового сообщения). Во время стрима ответ добавляется
        живой оценкой. Без единого хода — оценка по текущим messages.
        """
        if self.last_usage is not None:
            if self.is_streaming:
                return self.last_usage.prompt_tokens + self.streaming_out_estimate, True
            return (
                self.last_usage.prompt_tokens + self.last_usage.completion_tokens,
                self.last_usage.estimated,
            )
        messages = self.context_builder.build_messages(self.system_prompt, self.memory.history)
        return self._estimate_messages(messages), True

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
        """
        self.memory.add(Message(role=Role.USER, content=text))
        self._stream_text = ""
        self._stream_tcs = {}
        self._server_usage = None
        previous_usage = self.last_usage
        messages: list[Message] = []
        try:
            provider, model = self._config.resolve_model(self.settings.model)
            messages = self.context_builder.build_messages(self.system_prompt, self.memory.history)
            # живая оценка контекста — видна в статус-баре ещё до ответа API
            self.last_usage = TokenUsage(
                prompt_tokens=self._estimate_messages(messages), estimated=True
            )
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
                if chunk.content:
                    self._stream_text += chunk.content
                self._accumulate_tool_calls(chunk)
            self.memory.add(
                Message(
                    role=Role.ASSISTANT,
                    content=self._stream_text or None,
                    tool_calls=self.streaming_tool_calls or None,
                )
            )
            self._finalize_usage(messages, assistant_added=True)
            return self._stream_text
        except asyncio.CancelledError:
            if self._stream_text:
                self.memory.add(
                    Message(role=Role.ASSISTANT, content=self._stream_text + CANCELLED_MARK)
                )
            self._finalize_usage(messages, assistant_added=bool(self._stream_text))
            raise
        except Exception:
            self.last_usage = previous_usage  # ход не состоялся — показываем прошлый
            raise
        finally:
            self._stream_text = ""
            self._stream_tcs = {}

    def _finalize_usage(self, messages: list[Message], *, assistant_added: bool) -> None:
        """Фиксирует токены завершившегося хода и привязывает их к ответу ассистента."""
        if self._server_usage is not None:
            sent_estimate = self._estimate_messages(messages)
            self.last_usage = TokenUsage(
                prompt_tokens=self._server_usage.prompt_tokens,
                completion_tokens=self._server_usage.completion_tokens,
                estimated=False,
                truncated=self._server_usage.prompt_tokens < sent_estimate * TRUNCATION_RATIO,
            )
        else:
            self.last_usage = TokenUsage(
                prompt_tokens=self._estimate_messages(messages),
                completion_tokens=self._estimate_text(self._stream_text),
                estimated=True,
            )
        if assistant_added:
            self.message_usage[len(self.memory.history) - 1] = self.last_usage

    @staticmethod
    def _estimate_text(text: str) -> int:
        """Грубая оценка: ~4 символа на токен."""
        return len(text) // CHARS_PER_TOKEN

    @classmethod
    def _estimate_messages(cls, messages: list[Message]) -> int:
        """Оценка контекста запроса по сериализованным сообщениям."""
        return sum(
            len(json.dumps(m.to_api(), ensure_ascii=False)) // CHARS_PER_TOKEN
            for m in messages
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
        """Экспортирует сессию (история + настройки + промпт) в jsonl-файл."""
        return save_session(
            path,
            settings=self.settings,
            system_prompt=self.system_prompt,
            name=self.name,
            history=self.memory.history,
        )

    def apply_session(self, data: SessionData) -> None:
        if data.name:
            self.name = data.name
        self.settings = data.settings
        self.system_prompt = data.system_prompt
        self.memory.clear()
        for message in data.history:
            self.memory.add(message)
        self.last_usage = None
        self.message_usage = {}

    # --- настройка через команды (runtime-override) ---

    def set_model(self, model_id: str) -> None:
        """Валидирует id модели против config и применяет к агенту."""
        self._config.resolve_model(model_id)  # ValueError при неизвестной
        self.settings.model = model_id

    def rename(self, name: str) -> None:
        self.name = name

    def set_system_prompt_file(self, path: str) -> None:
        """Заменяет системный промпт содержимым файла (FileNotFoundError пробрасывается)."""
        self.system_prompt = Path(path).read_text(encoding="utf-8")
