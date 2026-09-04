import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .errors import ConfigError

load_dotenv()

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_CHAT_PATH = "/chat/completions"


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

    @classmethod
    def from_env(cls) -> "Settings":
        base_url = os.getenv("API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        api_key = os.getenv("API_KEY") or None
        model = os.getenv("API_MODEL", DEFAULT_MODEL)
        chat_path = _resolve_chat_path(base_url, os.getenv("API_CHAT_PATH"))

        if not base_url.startswith(("http://", "https://")):
            raise ConfigError(
                f"API_BASE_URL должен начинаться с http:// или https://, получено: {base_url!r}"
            )
        return cls(base_url=base_url, api_key=api_key, model=model, chat_path=chat_path)


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_CHAT_PATH", "DEFAULT_MODEL", "Settings"]
