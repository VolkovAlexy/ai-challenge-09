"""Pydantic-схемы конфигурации: multi-provider config.json и настройки агента."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import ErrorDetails


class Provider(BaseModel):
    """Один именованный провайдер (любой OpenAI-compatible эндпоинт)."""

    api_base: str = Field(description="Базовый URL, клиент сам дописывает /chat/completions")
    api_key: str = Field(default="", description="Может быть пустым (например, для ollama)")
    models: list[str] = Field(default_factory=list, description="Список доступных моделей")

    @field_validator("api_base")
    @classmethod
    def _api_base_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("api_base не может быть пустым")
        return v.rstrip("/")


class Config(BaseModel):
    """Глобальный конфиг (config.json): провайдеры + дефолтные параметры генерации."""

    providers: dict[str, Provider] = Field(min_length=1)
    default_model: str = Field(description="Модель в формате provider:model")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    stop: list[str] = Field(default_factory=list)

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


class AgentSettings(BaseModel):
    """Настройки конкретного агента (клон дефолтов config + runtime-override)."""

    model: str = Field(description="Модель в формате provider:model")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    stop: list[str] = Field(default_factory=list)

    @classmethod
    def from_config(cls, config: Config) -> AgentSettings:
        """Клон дефолтов из глобального конфига."""
        return cls(
            model=config.default_model,
            temperature=config.temperature,
            top_p=config.top_p,
            max_tokens=config.max_tokens,
            stop=list(config.stop),
        )
