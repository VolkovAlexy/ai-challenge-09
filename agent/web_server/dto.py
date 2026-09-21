"""Pydantic-схемы запросов/ответов web-бэкенда (зеркало WEB_UI.MD §5.1).

Поля match frontend-типам из web/src/api/types.ts. Ни один DTO не содержит
`api_key` — ключи остаются только в backend.
"""

from __future__ import annotations

from pydantic import BaseModel


class UsageDTO(BaseModel):
    """Потребление токенов одного хода."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    approx: bool | None = None  # числа получены локальной оценкой, не от API


class MessageDTO(BaseModel):
    """Одно сообщение истории, отданное фронтенду."""

    id: str
    role: str
    content: str
    usage: UsageDTO | None = None
    error: dict[str, str] | None = None
    # имя инструмента для роли tool (результат чьего вызова)
    tool_name: str | None = None
    # имена инструментов, вызванных ассистентом (сообщение с tool_calls)
    tool_calls: list[str] | None = None


class AgentSettingsDTO(BaseModel):
    """Настройки генерации агента (без стратегии контекста — её фронт не знает)."""

    temperature: float
    top_p: float
    max_tokens: int
    stop: list[str]


class SystemPromptDTO(BaseModel):
    """Системный промпт: путь к файлу и его содержимое."""

    path: str
    content: str


class AgentDTO(BaseModel):
    """Полное состояние агента для /api/agents."""

    id: str
    name: str
    model: str
    settings: AgentSettingsDTO
    system_prompt: SystemPromptDTO
    context_used: int
    context_window: int
    streaming: bool
    compacting: bool
    scratchpad: str = ""  # рабочая память текущей задачи
    memory_suggestion: str | None = None  # предложение сохранить знание (ждёт решения UI)


class LongTermDTO(BaseModel):
    """Долговременная память: путь к файлу, содержимое, записи (строки-буллеты)."""

    path: str
    content: str
    entries: list[str]


class ProviderDTO(BaseModel):
    """Один провайдер: базовый URL и модели с их контекстными окнами."""

    api_base: str
    models: dict[str, dict[str, int]]  # model -> {"context_window": int} (пустое — окно неизвестно)


class ConfigDTO(BaseModel):
    """Глобальный конфиг без api_key."""

    providers: dict[str, ProviderDTO]
    default_model: str
    temperature: float
    top_p: float
    max_tokens: int
    stop: list[str]
    context_window_default: int
    compaction_threshold: float
    sliding_window: int


class CommandDTO(BaseModel):
    """Описание команды для палитры команд."""

    name: str
    description: str
    args_spec: str


class SessionInfoDTO(BaseModel):
    """Метаданные сессии для палитры /session и боковой панели."""

    id: str
    title: str
    updated_at: str
    model: str | None = None
    message_count: int | None = None


# --- запросы ---


class CreateAgentRequest(BaseModel):
    name: str | None = None


class SendMessageRequest(BaseModel):
    content: str


class PatchAgentRequest(BaseModel):
    name: str | None = None
    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    system_prompt_path: str | None = None


class SystemPromptPutRequest(BaseModel):
    path: str


class LoadSessionRequest(BaseModel):
    session_id: str


class SessionPatchRequest(BaseModel):
    title: str


class ExportRequest(BaseModel):
    path: str | None = None


class RememberRequest(BaseModel):
    content: str


class ScratchpadPutRequest(BaseModel):
    content: str


class ForkRequest(BaseModel):
    message_index: int
