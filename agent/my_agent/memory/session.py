"""Сессионная память: история диалога + save/load в jsonl.

Формат файла: первая строка — meta (настройки агента, имя, системный промпт),
далее по строке на сообщение. Валидация через pydantic-модели.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from my_agent.config.schema import AgentSettings
from my_agent.core.message import Message


class SessionError(Exception):
    """Ошибка save/load сессии."""


@dataclass
class SessionData:
    """Восстановленное состояние сессии."""

    settings: AgentSettings
    system_prompt: str = ""
    name: str | None = None
    history: list[Message] = field(default_factory=list)


class InMemorySession:
    """История диалога одного агента (plain Python, без UI)."""

    def __init__(self) -> None:
        self._history: list[Message] = []

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    def add(self, message: Message) -> None:
        self._history.append(message)

    def clear(self) -> None:
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)


def save_session(
    path: Path | str,
    *,
    settings: AgentSettings,
    system_prompt: str,
    name: str | None = None,
    history: list[Message],
) -> Path:
    """Пишет сессию в jsonl; возвращает путь."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "type": "meta",
        "name": name,
        "system_prompt": system_prompt,
        "agent": settings.model_dump(),
    }
    lines = [json.dumps(meta, ensure_ascii=False)]
    for message in history:
        entry = json.dumps(
            {"type": "message", "message": message.model_dump()}, ensure_ascii=False
        )
        lines.append(entry)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def load_session(path: Path | str) -> SessionData:
    """Читает jsonl-сессию; при повреждённом файле — SessionError."""
    path = Path(path)
    if not path.exists():
        raise SessionError(f"файл сессии не найден: {path}")
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        meta = json.loads(lines[0])
        if meta.get("type") != "meta":
            raise SessionError(f"{path}: первая строка должна быть meta")
        settings = AgentSettings.model_validate(meta["agent"])
        history = [
            Message.model_validate(entry["message"])
            for entry in (json.loads(line) for line in lines[1:])
            if entry.get("type") == "message"
        ]
        return SessionData(
            settings=settings,
            system_prompt=meta.get("system_prompt", ""),
            name=meta.get("name"),
            history=history,
        )
    except (json.JSONDecodeError, KeyError, IndexError, ValueError, OSError) as exc:
        raise SessionError(f"не удалось прочитать сессию {path}: {exc}") from exc
