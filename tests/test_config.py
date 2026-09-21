"""Валидация config.json (multi-provider) и store."""

import json

import pytest

from agent.config.schema import AgentSettings, Config, validate_config
from agent.config.store import ConfigError, default_config, load_config, save_config


def valid_dict() -> dict:
    return {
        "providers": {
            "openai": {
                "api_base": "https://api.openai.com/v1",
                "api_key": "sk-x",
                "models": ["gpt-4o-mini"],
            },
            "ollama": {
                "api_base": "http://localhost:11434/v1",
                "api_key": "",
                "models": ["llama3.1"],
            },
        },
        "default_model": "openai:gpt-4o-mini",
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 4096,
        "stop": [],
    }


def test_valid_config() -> None:
    cfg = validate_config(valid_dict())
    assert set(cfg.providers) == {"openai", "ollama"}
    assert cfg.temperature == 0.7


def test_ollama_empty_api_key_allowed() -> None:
    cfg = validate_config(valid_dict())
    assert cfg.providers["ollama"].api_key == ""


def test_default_model_unknown_provider() -> None:
    data = valid_dict()
    data["default_model"] = "mistral:m1"
    with pytest.raises(ValueError, match="провайдер 'mistral' не найден"):
        validate_config(data)


def test_default_model_unknown_model() -> None:
    data = valid_dict()
    data["default_model"] = "openai:gpt-9"
    with pytest.raises(ValueError, match="модель 'gpt-9' не найдена"):
        validate_config(data)


def test_default_model_bad_format() -> None:
    data = valid_dict()
    data["default_model"] = "gpt-4o-mini"
    with pytest.raises(ValueError, match="provider:model"):
        validate_config(data)


def test_no_providers() -> None:
    with pytest.raises(ValueError, match="providers"):
        validate_config({"providers": {}, "default_model": "a:b"})


def test_temperature_out_of_range() -> None:
    data = valid_dict()
    data["temperature"] = 2.5
    with pytest.raises(ValueError, match="temperature"):
        validate_config(data)


def test_api_base_normalized() -> None:
    data = valid_dict()
    data["providers"]["openai"]["api_base"] = "https://api.openai.com/v1/"
    cfg = validate_config(data)
    assert cfg.providers["openai"].api_base == "https://api.openai.com/v1"


def test_all_model_ids_sorted() -> None:
    cfg = validate_config(valid_dict())
    assert cfg.all_model_ids() == ["ollama:llama3.1", "openai:gpt-4o-mini"]


def test_resolve_model() -> None:
    cfg = validate_config(valid_dict())
    provider, model = cfg.resolve_model("ollama:llama3.1")
    assert model == "llama3.1"
    assert provider.api_base == "http://localhost:11434/v1"
    with pytest.raises(ValueError):
        cfg.resolve_model("nope:x")


def test_agent_settings_from_config() -> None:
    cfg = validate_config(valid_dict())
    settings = AgentSettings.from_config(cfg)
    assert settings.model == "openai:gpt-4o-mini"
    assert settings.temperature == 0.7
    # клон: изменение не трогает конфиг
    settings.temperature = 0.1
    assert AgentSettings.from_config(cfg).temperature == 0.7


def test_context_strategy_defaults_and_validation() -> None:
    cfg = validate_config(valid_dict())
    assert cfg.context_strategy == "summary"  # дефолт — текущее поведение
    assert cfg.sliding_window == 20
    assert validate_config({**valid_dict(), "context_strategy": "facts"}).context_strategy == (
        "facts"
    )
    with pytest.raises(ValueError):
        validate_config({**valid_dict(), "context_strategy": "magic"})
    with pytest.raises(ValueError):
        validate_config({**valid_dict(), "sliding_window": 0})
    settings = AgentSettings.from_config(cfg)
    assert settings.context_strategy == "summary"
    assert settings.sliding_window == 20


def test_load_config_creates_default(tmp_path) -> None:
    path = tmp_path / "config.json"
    cfg = load_config(path)
    assert path.exists()
    assert cfg.default_model in cfg.all_model_ids()


def test_save_load_roundtrip(tmp_path) -> None:
    path = tmp_path / "config.json"
    original = validate_config(valid_dict())
    save_config(original, path)
    assert load_config(path) == original


def test_load_config_invalid_json(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON"):
        load_config(path)


def test_load_config_invalid_fields_message(tmp_path) -> None:
    path = tmp_path / "config.json"
    data = valid_dict()
    data["temperature"] = 5
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="temperature"):
        load_config(path)


def test_default_config_is_valid() -> None:
    cfg = default_config()
    assert isinstance(cfg, Config)
    assert cfg.default_model in cfg.all_model_ids()
