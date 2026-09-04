import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .errors import ConfigError

load_dotenv()

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CHAT_PATH = "/chat/completions"
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0


def parse_temperature(value: str) -> float:
    """Парсит температуру и проверяет диапазон 0.0–2.0."""
    try:
        temperature = float(value)
    except ValueError:
        raise ConfigError(f"Температура должна быть числом, получено: {value!r}") from None
    if not MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE:
        raise ConfigError(
            f"Температура должна быть в диапазоне {MIN_TEMPERATURE}–{MAX_TEMPERATURE}, "
            f"получено: {temperature}"
        )
    return temperature


def _resolve_chat_path(base_url: str, explicit: str | None) -> str:
    """Путь эндпоинта: явный API_CHAT_PATH, либо расчёт по base_url.

    Если base_url заканчивается на /v1 (стиль OpenAI) -> /chat/completions,
    иначе (обычные локальные серверы) -> /v1/chat/completions.
    """
    if explicit:
        return "/" + explicit.strip("/")
    if base_url.rstrip("/").endswith("/v1"):
        return DEFAULT_CHAT_PATH
    return "/v1/chat/completions"


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str | None
    model: str
    chat_path: str = DEFAULT_CHAT_PATH
    temperature: float | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv("API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        api_key = os.getenv("API_KEY") or None
        model = os.getenv("API_MODEL", DEFAULT_MODEL)
        chat_path = _resolve_chat_path(base_url, os.getenv("API_CHAT_PATH"))
        raw_temperature = (os.getenv("API_TEMPERATURE") or "").strip()
        temperature = parse_temperature(raw_temperature) if raw_temperature else None

        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"API_BASE_URL должен начинаться с http:// или https://, получено: {base_url!r}"
            )
        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            chat_path=chat_path,
            temperature=temperature,
        )


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_CHAT_PATH",
    "DEFAULT_MODEL",
    "MAX_TEMPERATURE",
    "MIN_TEMPERATURE",
    "Settings",
    "parse_temperature",
]
