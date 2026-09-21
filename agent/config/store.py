"""Load/save config.json."""

from __future__ import annotations

import json
from pathlib import Path

from agent.config.schema import Config, validate_config

DEFAULT_CONFIG_PATH = Path("config.json")

_DEFAULT_CONFIG: dict[str, object] = {
    "providers": {
        "ollama": {
            "api_base": "http://localhost:11434/v1",
            "api_key": "",
            "models": {"llama3.1": 131072, "qwen2.5-coder": 32768},
        },
    },
    "default_model": "ollama:qwen2.5-coder",
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 4096,
    "stop": [],
    "context_window_default": 32768,
    "compaction_threshold": 0.6,
}


class ConfigError(Exception):
    """Ошибка чтения/валидации config.json."""


def default_config() -> Config:
    """Валидный конфиг по умолчанию (один локальный ollama-провайдер)."""
    return validate_config(json.loads(json.dumps(_DEFAULT_CONFIG)))


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    """Читает и валидирует config.json. Файла нет — пишет дефолтный и возвращает его."""
    path = Path(path)
    if not path.exists():
        save_config(default_config(), path)
        return default_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json: некорректный JSON: {exc}") from exc
    try:
        return validate_config(data)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def save_config(config: Config, path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Сериализует Config в config.json (human-readable)."""
    path = Path(path)
    path.write_text(
        json.dumps(config.model_dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
