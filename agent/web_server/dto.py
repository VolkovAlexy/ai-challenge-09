"""Pydantic-схемы запросов/ответов web-бэкенда (зеркало WEB_UI.MD §5.1).

Поля match frontend-типам из web/src/api/types.ts. Ни один DTO не содержит
`api_key` — ключи остаются только в backend.
"""

from __future__ import annotations

from enum import StrEnum

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
    # размышления thinking-моделей (стриминг в UI, в API-проекцию не уходят)
    reasoning: str | None = None
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


class TaskStateDTO(BaseModel):
    """Состояние задачи как конечный автомат (этап, шаг, ожидаемое действие)."""

    phase: str  # idle | planning | execution | validation | done
    step: int = 0  # номер текущего шага (1-based; 0 — шага нет)
    steps: list[str] = []  # план задачи: шаги ВЫПОЛНЕНИЯ
    validation_steps: list[str] = []  # план проверки: шаги ПРОВЕРКИ результата
    expected_action: str = ""  # что сделать дальше
    description: str = ""  # описание задачи
    paused: bool = False  # пауза на любом этапе
    plan_confirmed: bool = False  # план подтверждён пользователем (для входа в выполнение)


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
    project_id: str = ""  # проект, которому принадлежит агент (и его сессия)
    scratchpad: str = ""  # рабочая память текущей задачи
    memory_suggestion: str | None = None  # предложение сохранить знание (ждёт решения UI)
    active_profile_id: str = ""  # активный профиль роли чата ("" — без него)
    task: TaskStateDTO | None = None  # состояние задачи (автомат); None — задачи нет
    invariants: list[str] = []  # ограничения (инварианты) сессии


class ProfileDTO(BaseModel):
    """Глобальный профиль роли: имя + текст (обогащает/переопределяет базовый промпт)."""

    id: str
    name: str
    content: str


class LongTermDTO(BaseModel):
    """Долговременная память: содержимое и записи (строки-буллеты)."""

    project_id: str = ""
    path: str = ""
    content: str
    entries: list[str]


class ProjectDTO(BaseModel):
    """Проект: верхний уровень иерархии памяти (свои сессии и долгосрочная память)."""

    id: str
    name: str
    session_count: int
    updated_at: str
    profile_ids: list[str] = []  # профили, привязанные к проекту


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
    project_id: str = ""


# --- запросы ---


class CreateAgentRequest(BaseModel):
    name: str | None = None
    project_id: str | None = None


class CreateProjectRequest(BaseModel):
    name: str


class PatchProjectRequest(BaseModel):
    name: str


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
    active_profile_id: str | None = None


class ProfileRequest(BaseModel):
    """Создание профиля: имя + текст роли."""

    name: str
    content: str


class PatchProfileRequest(BaseModel):
    name: str | None = None
    content: str | None = None


class ProjectProfilesRequest(BaseModel):
    """Набор профилей проекта (полная замена привязки)."""

    profile_ids: list[str]


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


class TaskCommandOperation(StrEnum):
    """Операции над автоматом задачи (управляются из UI; без слэш-команд)."""

    START = "start"
    SET_PHASE = "set_phase"
    ADVANCE = "advance"
    PAUSE = "pause"
    RESUME = "resume"
    RESET = "reset"
    SET_EXPECTED_ACTION = "set_expected_action"
    CONFIRM_PLAN = "confirm_plan"


class TaskCommandRequest(BaseModel):
    """Команда управления автоматом задачи (POST /api/agents/{id}/task)."""

    operation: TaskCommandOperation
    # только для start
    description: str = ""
    # план задачи: список шагов выполнения
    steps: list[str] | None = None
    # план проверки: список шагов проверки (только для start / update)
    validation_steps: list[str] | None = None
    # для advance — отметить текущий шаг выполненным и перейти дальше
    done: bool = True
    # для set_phase / start — целевой этап
    phase: str = ""
    # ожидаемое действие при переходе
    expected_action: str = ""


class ForkRequest(BaseModel):
    message_index: int
