"""Pydantic-схемы конфигурации: multi-provider config.json и настройки агента."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import ErrorDetails

ContextStrategy = Literal["none", "summary", "sliding", "facts"]
"""Стратегия управления контекстом (проекцией истории для LLM).

- none — вся история без управления;
- summary — автокомпакция: старейший префикс сжимается в саммари;
- sliding — только последние N сообщений (sliding_window);
- facts — facts-блок (ключ-значение) + последние N сообщений.
"""

EmbeddingStrategy = Literal["paragraph", "fixed"]
"""Стратегия нарезки файлов знаний на чанки.

- paragraph — по абзацам (разделитель — пустая строка), короткие склеиваются;
- fixed — непрерывные куски фиксированного размера с перекрытием.
"""


class Provider(BaseModel):
    """Один именованный провайдер (любой OpenAI-compatible эндпоинт)."""

    api_base: str = Field(description="Базовый URL, клиент сам дописывает /chat/completions")
    api_key: str = Field(default="", description="Может быть пустым (например, для ollama)")
    models: dict[str, int | None] = Field(
        default_factory=dict,
        description="Модель → размер контекстного окна в токенах (None — окно неизвестно)",
    )

    @field_validator("models", mode="before")
    @classmethod
    def _normalize_models(cls, v: Any) -> Any:
        """Старый формат (список имён) → dict без известных окон (обратная совместимость)."""
        if isinstance(v, list):
            return {str(name): None for name in v}
        return v

    @field_validator("models")
    @classmethod
    def _windows_positive(cls, v: dict[str, int | None]) -> dict[str, int | None]:
        for name, window in v.items():
            if window is not None and window <= 0:
                raise ValueError(f"размер контекста модели '{name}' должен быть > 0")
        return v

    @field_validator("api_base")
    @classmethod
    def _api_base_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("api_base не может быть пустым")
        return v.rstrip("/")


class McpServer(BaseModel):
    """Описание одного MCP-сервера: транспорты stdio (команда) или http (url)."""

    transport: Literal["stdio", "http"] = Field(
        default="stdio", description="Транспорт подключения: stdio или streamable-http"
    )
    command: str | None = Field(default=None, description="Команда для stdio-транспорта")
    args: list[str] = Field(default_factory=list, description="Аргументы команды для stdio")
    url: str | None = Field(default=None, description="URL для http-транспорта")
    enabled: bool = Field(default=True, description="Включён ли сервер по умолчанию")

    @model_validator(mode="after")
    def _validate_transport_fields(self) -> McpServer:
        if self.transport == "stdio" and not self.command:
            raise ValueError("для stdio-транспорта требуется command")
        if self.transport == "http" and not self.url:
            raise ValueError("для http-транспорта требуется url")
        return self


class Config(BaseModel):
    """Глобальный конфиг (config.json): провайдеры + дефолтные параметры генерации."""

    providers: dict[str, Provider] = Field(min_length=1)
    default_model: str = Field(description="Модель в формате provider:model")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    stop: list[str] = Field(default_factory=list)
    context_window_default: int = Field(
        default=32768, gt=0, description="Окно по умолчанию, если у модели не указан размер"
    )
    compaction_threshold: float = Field(
        default=0.6,
        ge=0.5,
        le=1.0,
        description="Доля заполнения окна, при которой история сжимается в саммари",
    )
    context_strategy: ContextStrategy = Field(
        default="summary",
        description="Стратегия контекста для новых агентов (none/summary/sliding/facts)",
    )
    sliding_window: int = Field(
        default=20,
        gt=0,
        description="Размер скользящего окна в сообщениях (стратегии sliding/facts)",
    )
    mcp_servers: dict[str, McpServer] = Field(
        default_factory=dict, description="MCP-серверы (имя → описание подключения)"
    )
    watchdog: WatchdogConfig | None = Field(
        default=None, description="Фоновый сторож (None — выключен)"
    )
    scheduler: SchedulerConfig | None = Field(
        default=None, description="Планировщик: адрес доставки результатов (None — выключен)"
    )
    embedding_model: str | None = Field(
        default=None,
        description="Модель эмбедингов в формате provider:model (None — RAG выключен)",
    )
    knowledge_dir: str = Field(
        default="knowledge",
        description="Папка с файлами знаний для RAG-индекса (относительно CWD)",
    )
    rag_top_k: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Сколько релевантных чанков инжектить в контекст при каждом ходе",
    )
    chunk_strategy: EmbeddingStrategy = Field(
        default="paragraph",
        description="Стратегия нарезки файлов знаний на чанки (paragraph/fixed)",
    )
    chunk_size: int = Field(
        default=512,
        gt=0,
        description="Размер чанка в символах (стратегия fixed)",
    )
    chunk_overlap: int = Field(
        default=64,
        ge=0,
        description="Перекрытие соседних чанков в символах (стратегия fixed)",
    )
    embed_batch_size: int = Field(
        default=128,
        gt=0,
        description="Сколько чанков отправлять в одном эмбединг-запросе при индексации",
    )

    @model_validator(mode="after")
    def _default_model_exists(self) -> Config:
        provider, sep, model = self.default_model.partition(":")
        if not sep or not provider or not model:
            raise ValueError(
                f"default_model '{self.default_model}' должен быть в формате 'provider:model'"
            )
        known = self.providers.get(provider)
        if known is None:
            raise ValueError(
                f"default_model '{self.default_model}': провайдер '{provider}' не найден "
                f"(доступны: {', '.join(self.providers)})"
            )
        if model not in known.models:
            raise ValueError(
                f"default_model '{self.default_model}': модель '{model}' не найдена "
                f"у провайдера '{provider}' (доступны: {', '.join(known.models)})"
            )
        return self

    @model_validator(mode="after")
    def _embedding_model_exists(self) -> Config:
        if not self.embedding_model:
            return self
        provider, sep, model = self.embedding_model.partition(":")
        if not sep or not provider or not model:
            raise ValueError(
                f"embedding_model '{self.embedding_model}' должен быть в формате 'provider:model'"
            )
        if provider not in self.providers:
            raise ValueError(
                f"embedding_model '{self.embedding_model}': провайдер '{provider}' не найден "
                f"(доступны: {', '.join(self.providers)})"
            )
        return self

    def all_model_ids(self) -> list[str]:
        """Все модели вида 'provider:model' из конфига (отсортированно)."""
        return sorted(
            f"{name}:{model}"
            for name, provider in self.providers.items()
            for model in provider.models
        )

    def resolve_model(self, model_id: str) -> tuple[Provider, str]:
        """Резолвит 'provider:model' → (Provider, название модели)."""
        provider, sep, model = model_id.partition(":")
        if not sep or not provider or not model:
            raise ValueError(f"неверный формат модели '{model_id}', ожидается 'provider:model'")
        known = self.providers.get(provider)
        if known is None:
            raise ValueError(
                f"провайдер '{provider}' не найден (доступны: {', '.join(self.providers)})"
            )
        if model not in known.models:
            raise ValueError(
                f"модель '{model}' не найдена у провайдера '{provider}' "
                f"(доступны: {', '.join(known.models)})"
            )
        return known, model

    def resolve_embedding(self) -> tuple[Provider, str]:
        """Резолвит embedding_model 'provider:model' → (Provider, название модели).

        В отличие от `resolve_model`, модель эмбедингов НЕ обязана быть в
        чат-списке провайдера (она не craft-compatible и не показывается в
        палитре моделей); нужен только сам провайдер ради api_base/api_key.
        """
        if not self.embedding_model:
            raise ValueError("embedding_model не задан (RAG выключен)")
        provider, sep, model = self.embedding_model.partition(":")
        if not sep or not provider or not model:
            raise ValueError(
                f"embedding_model '{self.embedding_model}' должен быть в формате 'provider:model'"
            )
        known = self.providers.get(provider)
        if known is None:
            raise ValueError(
                f"embedding_model '{self.embedding_model}': провайдер '{provider}' не найден "
                f"(доступны: {', '.join(self.providers)})"
            )
        return known, model

    def context_window_for(self, model_id: str) -> int:
        """Окно модели: из config → context_window_default (ValueError, если модель неизвестна)."""
        _, model = self.resolve_model(model_id)
        window = self.providers[model_id.partition(":")[0]].models.get(model)
        return window if window is not None else self.context_window_default


def _format_error(error: ErrorDetails) -> str:
    loc = [str(part) for part in error["loc"] if not str(part).startswith("_")]
    field = loc[0] if loc else "config"
    message = error["msg"]
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return f"  {field}: {message}"


def validate_config(data: Any) -> Config:
    """Валидирует dict как Config; при ошибке — ValueError с понятным списком полей."""
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        lines = [_format_error(error) for error in exc.errors()]
        raise ValueError("невалидный config:\n" + "\n".join(lines)) from exc


class SchedulerConfig(BaseModel):
    """Параметры интеграции с планировщиком (mcp_scheduler).

    `notify_url` — адрес `POST /api/scheduler/notify` web-сервера, куда
    планировщик шлёт уведомления `job_added`/`job_ran`/`job_removed` для
    доставки результатов в сессию создателя. None — интеграция выключена.
    """

    notify_url: str = Field(
        default="http://127.0.0.1:8321/api/scheduler/notify",
        description="Адрес приёмника уведомлений планировщика",
    )


class WatchdogConfig(BaseModel):
    """Параметры фонового «сторожа»: периодический запуск хода агента.

    Каждый период сторож шлёт `prompt` выбранному агенту через `start_ask`
    (24/7-сценарий: агент сам по расписанию формирует сводку/данные). Если агент
    уже отвечает — тик пропускается. Управляется полем `enabled`.
    """

    enabled: bool = Field(default=True, description="Включён ли сторож")
    agent_id: str | None = Field(
        default=None, description="id агента; None — активный агент"
    )
    interval_seconds: float = Field(
        default=3600.0,
        gt=0,
        description="Период между срабатываниями (секунды)",
    )
    prompt: str = Field(description="Промпт, отправляемый агенту при срабатывании")


class AgentSettings(BaseModel):
    """Настройки конкретного агента (клон дефолтов config + runtime-override)."""

    model: str = Field(description="Модель в формате provider:model")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    stop: list[str] = Field(default_factory=list)
    context_strategy: ContextStrategy = Field(default="summary")
    sliding_window: int = Field(default=20, gt=0)
    compaction_threshold: float = Field(
        default=0.6,
        ge=0.5,
        le=1.0,
        description="Доля заполнения окна, при которой история сжимается в саммари",
    )

    @classmethod
    def from_config(cls, config: Config) -> AgentSettings:
        """Клон дефолтов из глобального конфига."""
        return cls(
            model=config.default_model,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            stop=list(config.stop),
            context_strategy=config.context_strategy,
            sliding_window=config.sliding_window,
            compaction_threshold=config.compaction_threshold,
        )
