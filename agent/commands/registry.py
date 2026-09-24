"""Команды как данные: реестр {name, description, handler, args_spec}.

Из реестра бесплатно строятся /help и tab-completion.
Команды настройки применяются к активному агенту.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from agent.core.agent import Agent

SESSIONS_DIR = Path("sessions")


class AppLike(Protocol):
    """Минимальные операции приложения, нужные командам (реализует Textual App)."""

    def active_agent(self) -> Agent | None: ...

    def close_agent(self, agent: Agent) -> bool: ...


@dataclass
class CommandContext:
    """Контекст для обработчика команды: приложение, активный агент, аргументы."""

    app: AppLike
    agent: Agent | None
    args: list[str] = field(default_factory=list)


CommandHandler = Callable[[CommandContext], str | None]
ArgCompleter = Callable[[CommandContext, str], list[str]]


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: CommandHandler
    args_spec: str = ""
    complete_arg: ArgCompleter | None = None


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> None:
        self._commands[command.name] = command

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        return sorted(self._commands.values(), key=lambda c: c.name)

    @staticmethod
    def parse_line(line: str) -> tuple[str, list[str]] | None:
        """'/name a b' → ('name', ['a', 'b']); не команда → None."""
        line = line.strip()
        if not line.startswith("/"):
            return None
        parts = line[1:].split()
        if not parts:
            return None
        return parts[0].lower(), parts[1:]

    def run(self, ctx: CommandContext, line: str) -> str | None:
        """Выполняет команду строкой; возвращает текст для вывода в чат."""
        parsed = self.parse_line(line)
        if parsed is None:
            return None
        name, args = parsed
        command = self.get(name)
        ctx.args = args
        if command is None:
            return "Неизвестная команда."
        return command.handler(ctx)

    def complete(self, ctx: CommandContext, line: str) -> list[str]:
        """Tab-completion: по имени команды или по аргументам."""
        line = line.lstrip()
        if not line.startswith("/"):
            return []
        body = line[1:]
        if " " in body:
            # После имени команды пробел — дополняем аргумент
            # (конечный пробел оставляем: '/model ' — пустой префикс аргумента).
            command_name, _, arg_text = body.partition(" ")
            command = self.get(command_name.strip().lower())
            if command is None or command.complete_arg is None:
                return []
            tokens = arg_text.split()
            return command.complete_arg(ctx, tokens[-1] if tokens else "")
        prefix = body.split()[0] if body else ""
        return [f"/{c.name}" for c in self.all() if c.name.startswith(prefix)]


def _require_agent(ctx: CommandContext) -> Agent | None:
    return ctx.agent


def default_registry() -> CommandRegistry:
    """Реестр команд: остаются только close и export."""
    registry = CommandRegistry()
    close_armed: set[int] = set()

    def _close(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        if agent.is_streaming and id(agent) not in close_armed:
            close_armed.add(id(agent))
            return (
                "У агента незавершённый запрос. "
                "Повторите /close, чтобы отменить запрос и закрыть вкладку."
            )
        close_armed.discard(id(agent))
        if ctx.app.close_agent(agent):
            return f"Агент '{agent.name}' закрыт."
        return "Не удалось закрыть агента."

    def _export(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        try:
            if ctx.args:
                target: str | Path = ctx.args[0]
            else:
                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                target = SESSIONS_DIR / f"{ts}.jsonl"
            saved = agent.export(target)
        except OSError as exc:
            return f"Ошибка экспорта: {exc}"
        return f"Сессия экспортирована: {saved}"

    registry.register(Command("close", "закрыть активного агента", _close))
    registry.register(
        Command("export", "экспортировать сессию активного агента в jsonl", _export, "[file]")
    )
    return registry
