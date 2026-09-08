"""Класс Agent: инстанс на каждый чат.

Свои настройки (клон дефолтов config + runtime-override), свой системный
промпт, свой SessionMemory. Общие ресурсы (LLMClient, ToolRegistry, Config)
— передаются в конструктор и шарятся между агентами.
"""

from __future__ import annotations

import asyncio
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
)
from my_agent.llm.client import LLMClient
from my_agent.memory.session import InMemorySession, SessionData, save_session
from my_agent.tools.registry import ToolRegistry

CANCELLED_MARK = "… (запрос отменён)"


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

    # --- состояние стриминга (читает UI) ---

    @property
    def is_streaming(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def streaming_text(self) -> str:
        """Частичный вывод текущего запроса (для catch-up при переключении вкладок)."""
        return self._stream_text

    @property
    def streaming_tool_calls(self) -> list[ToolCall]:
        return list(self._stream_tcs.values())

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
        """
        self.memory.add(Message(role=Role.USER, content=text))
        self._stream_text = ""
        self._stream_tcs: dict[int, ToolCall] = {}
        try:
            provider, model = self._config.resolve_model(self.settings.model)
            messages = self.context_builder.build_messages(self.system_prompt, self.memory.history)
            request = ChatRequest(
                model=model,
                messages=messages,
                temperature=self.settings.temperature,
                top_p=self.settings.top_p,
                max_tokens=self.settings.max_tokens,
                stop=self.settings.stop or None,
            )
            async for chunk in self._llm.astream(request, provider.api_base, provider.api_key):
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
            return self._stream_text
        except asyncio.CancelledError:
            if self._stream_text:
                self.memory.add(
                    Message(role=Role.ASSISTANT, content=self._stream_text + CANCELLED_MARK)
                )
            raise
        finally:
            self._stream_text = ""
            self._stream_tcs = {}

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
