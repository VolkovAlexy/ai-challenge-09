"""Сессионная память: история диалога + саммари + facts + ветки + save/load в jsonl.

Формат файла: первая строка — meta (настройки агента, имя, системный промпт,
саммари сжатого префикса и его длина, facts, активная ветка и метаданные всех
веток), далее по строке на сообщение. Сообщения неактивных веток помечены
`"branch": <имя>`; сообщения активной ветки идут без метки (совместимо со
старыми файлами). Валидация через pydantic-модели.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config.schema import AgentSettings
from agent.core.message import Message
from agent.core.task import TaskState, TaskStateMachine
from agent.memory.branching import DEFAULT_BRANCH, BranchState


class SessionError(Exception):
    """Ошибка save/load сессии."""


@dataclass
class SessionData:
    """Восстановленное состояние сессии.

    Поля history/summary/compacted_upto/facts описывают активную ветку;
    `branches` — только неактивные ветки (активная — в самих полях).
    `scratchpad` — рабочая память задачи (уровень сессии, не ветки).
    `task` — состояние задачи как конечный автомат (уровень сессии).
    """

    settings: AgentSettings
    system_prompt: str = ""
    name: str | None = None
    summary: str | None = None
    compacted_upto: int = 0
    facts: dict[str, str] = field(default_factory=dict)
    scratchpad: str = ""
    invariants: list[str] = field(default_factory=list)  # ограничения (инварианты) сессии
    task: TaskState | None = None
    active_branch: str = DEFAULT_BRANCH
    branches: dict[str, BranchState] = field(default_factory=dict)
    history: list[Message] = field(default_factory=list)
    active_profile_id: str = ""  # активный профиль роли чата


class InMemorySession:
    """История диалога одного агента (plain Python, без UI).

    История хранится целиком и никогда не урезается — чат всегда показывает
    полную беседу. Для LLM отправляется проекция: саммари сжатого префикса
    (compacted_upto сообщений с начала) + несжатый хвост (стратегия summary)
    либо скользящее окно (sliding/facts) — см. Agent._projection.

    Ветки: активная ветка — в полях ниже; неактивные — в `self._branches`
    (BranchState). fork() сохраняет чекпоинт в новую ветку и переключается
    на неё, switch() — возвращает сохранённое состояние.
    """

    def __init__(self) -> None:
        self._history: list[Message] = []
        self.summary: str | None = None
        self.compacted_upto: int = 0
        self.facts: dict[str, str] = {}
        self.scratchpad: str = ""  # рабочая память задачи (уровень сессии)
        self.invariants: list[str] = []  # ограничения (уровень сессии, не ветки)
        self.task: TaskStateMachine = TaskStateMachine()  # автомат состояния задачи
        self._branches: dict[str, BranchState] = {}
        self._active: str = DEFAULT_BRANCH

    # --- активная ветка (обычная работа с историей) ---

    @property
    def history(self) -> list[Message]:
        return list(self._history)

    @property
    def tail(self) -> list[Message]:
        """Несжатая часть истории — то, что уходит в LLM помимо саммари."""
        return self._history[self.compacted_upto :]

    def add(self, message: Message) -> None:
        self._history.append(message)

    def compact_prefix(self, count: int, *, summary: str) -> None:
        """Помечает count старейших сообщений сжатыми (заменяются саммари для LLM)."""
        if count <= 0:
            return
        self.compacted_upto += count
        self.summary = summary

    def truncate_to(self, size: int) -> None:
        """Обрезает активную ветку до size сообщений (ветвление от сообщения).

        Если сжатый префикс частично выходит за новую историю — саммари
        сбрасывается: оно описывает сообщения, которых в ветке больше нет.
        """
        self._history = self._history[:size]
        if self.compacted_upto > size:
            self.compacted_upto = 0
            self.summary = None

    def clear(self, *, summary: str | None = None, compacted_upto: int = 0) -> None:
        """Очищает активную ветку (другие ветки сохраняются)."""
        self._history.clear()
        self.summary = summary
        self.compacted_upto = compacted_upto
        self.facts = {}
        self.scratchpad = ""
        self.invariants = []
        self.task = TaskStateMachine()

    def __len__(self) -> int:
        return len(self._history)

    # --- ветки ---

    @property
    def active_branch(self) -> str:
        return self._active

    @property
    def branches(self) -> dict[str, BranchState]:
        """Неактивные ветки (активная — в полях инстанса)."""
        return dict(self._branches)

    def branch_names(self) -> list[str]:
        """Все имена веток, активная первой (для /branches и completion)."""
        return [self._active, *sorted(self._branches)]

    def branch_info(self) -> list[tuple[str, int, bool]]:
        """(имя, число сообщений, активная?) по всем веткам."""
        info = [
            (name, len(state.history), False) for name, state in sorted(self._branches.items())
        ]
        return [(self._active, len(self._history), True), *info]

    def fork(self, name: str) -> None:
        """Checkpoint: копия текущего диалога в новую ветку + переключение на неё.

        Активная ветка сохраняется под своим именем; новая ветка получает
        копию состояния (история, саммари, facts) и становится активной.
        """
        if name == self._active or name in self._branches:
            raise ValueError(f"ветка '{name}' уже существует")
        self._save_active()
        current = self._branches[self._active]
        self._branches[name] = BranchState(
            history=list(current.history),
            summary=current.summary,
            compacted_upto=current.compacted_upto,
            facts=dict(current.facts),
        )
        self._switch_to(name)

    def switch(self, name: str) -> None:
        """Переключается на сохранённую ветку (текущая сохраняется)."""
        if name == self._active:
            return
        if name not in self._branches:
            raise KeyError(f"ветка '{name}' не найдена")
        self._save_active()
        self._switch_to(name)

    def restore_branches(
        self, branches: dict[str, BranchState], *, active: str = DEFAULT_BRANCH
    ) -> None:
        """Загружает неактивные ветки и имя активной (при /session и load)."""
        self._branches = {name: state for name, state in branches.items() if name != active}
        self._active = active

    def _save_active(self) -> None:
        self._branches[self._active] = BranchState(
            history=self._history,
            summary=self.summary,
            compacted_upto=self.compacted_upto,
            facts=dict(self.facts),
        )

    def _switch_to(self, name: str) -> None:
        state = self._branches.pop(name)
        self._history = state.history
        self.summary = state.summary
        self.compacted_upto = state.compacted_upto
        self.facts = dict(state.facts)
        self._active = name


def save_session(
    path: Path | str,
    *,
    settings: AgentSettings,
    system_prompt: str,
    name: str | None = None,
    summary: str | None = None,
    compacted_upto: int = 0,
    history: list[Message],
    facts: dict[str, str] | None = None,
    scratchpad: str = "",
    invariants: list[str] | None = None,
    task: TaskState | None = None,
    active_branch: str = DEFAULT_BRANCH,
    branches: dict[str, BranchState] | None = None,
    active_profile_id: str = "",
) -> Path:
    """Пишет сессию в jsonl; возвращает путь.

    `history` — история активной ветки; `branches` — неактивные ветки
    (BranchState с собственной историей).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    branch_meta: dict[str, dict[str, object]] = {
        active_branch: {
            "summary": summary,
            "compacted_upto": compacted_upto,
            "facts": facts or {},
        }
    }
    for branch_name, state in (branches or {}).items():
        if branch_name == active_branch:
            continue
        branch_meta[branch_name] = {
            "summary": state.summary,
            "compacted_upto": state.compacted_upto,
            "facts": state.facts,
        }
    meta = {
        "type": "meta",
        "name": name,
        "system_prompt": system_prompt,
        "summary": summary,
        "compacted_upto": compacted_upto,
        "facts": facts or {},
        "scratchpad": scratchpad,
        "invariants": invariants or [],
        "task": task.to_dict() if task is not None and task.is_active else None,
        "active_branch": active_branch,
        "branches": branch_meta,
        "active_profile_id": active_profile_id,
        "agent": settings.model_dump(),
    }
    lines = [json.dumps(meta, ensure_ascii=False)]
    for message in history:
        entry = json.dumps(
            {"type": "message", "message": message.model_dump()}, ensure_ascii=False
        )
        lines.append(entry)
    for branch_name, state in (branches or {}).items():
        if branch_name == active_branch:
            continue
        for message in state.history:
            entry = json.dumps(
                {"type": "message", "branch": branch_name, "message": message.model_dump()},
                ensure_ascii=False,
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
        active = meta.get("active_branch", DEFAULT_BRANCH)
        active_history: list[Message] = []
        branch_histories: dict[str, list[Message]] = {}
        for entry in (json.loads(line) for line in lines[1:]):
            if entry.get("type") != "message":
                continue
            message = Message.model_validate(entry["message"])
            branch = entry.get("branch", active)
            if branch == active:
                active_history.append(message)
            else:
                branch_histories.setdefault(branch, []).append(message)
        branch_meta: dict[str, Any] = meta.get("branches", {})
        branches = {
            branch_name: BranchState(
                history=branch_histories.get(branch_name, []),
                summary=state.get("summary"),
                compacted_upto=int(state.get("compacted_upto", 0)),
                facts={str(k): str(v) for k, v in state.get("facts", {}).items()},
            )
            for branch_name, state in branch_meta.items()
            if branch_name != active
        }
        return SessionData(
            settings=settings,
            system_prompt=meta.get("system_prompt", ""),
            name=meta.get("name"),
            summary=meta.get("summary"),
            compacted_upto=meta.get("compacted_upto", 0),
            facts={str(k): str(v) for k, v in meta.get("facts", {}).items()},
            scratchpad=str(meta.get("scratchpad", "")),
            invariants=[str(x) for x in meta.get("invariants", [])],
            task=TaskState.from_dict(meta.get("task", {})),
            active_branch=active,
            branches=branches,
            history=active_history,
            active_profile_id=str(meta.get("active_profile_id", "")),
        )
    except (json.JSONDecodeError, KeyError, IndexError, ValueError, OSError, TypeError) as exc:
        raise SessionError(f"не удалось прочитать сессию {path}: {exc}") from exc
