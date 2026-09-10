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

from my_agent.config.schema import Config
from my_agent.core.agent import Agent

SESSIONS_DIR = Path("sessions")


class AppLike(Protocol):
    """Минимальные операции приложения, нужные командам (реализует Textual App)."""

    def active_agent(self) -> Agent | None: ...

    def agent_count(self) -> int: ...

    def add_agent(self, name: str) -> Agent: ...

    def close_agent(self, agent: Agent) -> bool: ...

    def quit(self) -> None: ...

    def config(self) -> Config: ...

    def open_session_palette(self) -> None: ...


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


def _command_list(registry: CommandRegistry) -> str:
    return "\n".join(f"  /{c.name} {c.args_spec}".ljust(28) + c.description for c in registry.all())


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
            return f"Команда /{name} не найдена. /help — список команд."
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


def _model_ids(ctx: CommandContext) -> list[str]:
    return ctx.app.config().all_model_ids()


def default_registry() -> CommandRegistry:
    """Реестр со всеми командами v1."""
    registry = CommandRegistry()
    close_armed: set[int] = set()

    def _help(ctx: CommandContext) -> str | None:
        return f"Команды:\n{_command_list(registry)}"

    def _new(ctx: CommandContext) -> str | None:
        name = " ".join(ctx.args).strip() or f"chat-{ctx.app.agent_count() + 1}"
        agent = ctx.app.add_agent(name)
        return f"Создан агент '{agent.name}' (модель {agent.settings.model})."

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

    def _name(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        if not ctx.args:
            return "Использование: /name <имя>"
        agent.rename(" ".join(ctx.args))
        return f"Агент переименован в '{agent.name}'."

    def _model(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if not ctx.args:
            current = agent.settings.model if agent else None
            return "Модели:\n" + "\n".join(
                f"  {'* ' if m == current else '  '}{m}" for m in _model_ids(ctx)
            )
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        model_id = ctx.args[0]
        try:
            agent.set_model(model_id)
        except ValueError as exc:
            return f"Ошибка: {exc}\n\nМодели:\n" + "\n".join(f"  {m}" for m in _model_ids(ctx))
        return None  # модель и так видна в статус-баре — заметка не нужна

    def _temperature(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None or len(ctx.args) != 1:
            return "Использование: /temperature <0..2>"
        try:
            value = float(ctx.args[0])
        except ValueError:
            return f"'{ctx.args[0]}' — не число."
        if not 0.0 <= value <= 2.0:
            return "temperature должен быть в диапазоне 0..2."
        agent.settings.temperature = value
        return f"temperature: {value}"

    def _top_p(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None or len(ctx.args) != 1:
            return "Использование: /top-p <0..1>"
        try:
            value = float(ctx.args[0])
        except ValueError:
            return f"'{ctx.args[0]}' — не число."
        if not 0.0 <= value <= 1.0:
            return "top_p должен быть в диапазоне 0..1."
        agent.settings.top_p = value
        return f"top_p: {value}"

    def _max_tokens(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None or len(ctx.args) != 1:
            return "Использование: /max-tokens <n>"
        try:
            value = int(ctx.args[0])
        except ValueError:
            return f"'{ctx.args[0]}' — не целое число."
        if value <= 0:
            return "max_tokens должен быть положительным."
        agent.settings.max_tokens = value
        return f"max_tokens: {value}"

    def _stop(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        raw = " ".join(ctx.args).strip()
        if raw in ("", '""'):
            agent.settings.stop = []
            return "stop-sequences очищены."
        sequences = [s.strip() for s in raw.split(",") if s.strip()]
        agent.settings.stop = sequences
        return f"stop-sequences: {agent.settings.stop}"

    def _system(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        if not ctx.args:
            prompt = agent.system_prompt
            if len(prompt) > 500:
                return f"Системный промпт ({len(prompt)} символов):\n{prompt[:500]}\n…"
            return f"Системный промпт:\n{prompt}"
        path = Path(ctx.args[0])
        if not path.exists():
            return f"Файл не найден: {path}"
        agent.set_system_prompt_file(str(path))
        return f"Системный промпт заменён на {path} ({len(agent.system_prompt)} символов)."

    def _history(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        history = agent.memory.history
        if not history:
            return "История пуста."
        lines = [f"История ({len(history)} сообщ.):"]
        for message in history:
            text = (message.content or "").replace("\n", " ")
            if len(text) > 200:
                text = text[:200] + "…"
            lines.append(f"  [{message.role.value}] {text}")
        return "\n".join(lines)

    def _clear(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        agent.memory.clear()
        agent.reset_totals()  # счётчики in/out/Σ описывают текущий диалог
        return "История очищена."

    def _compact(ctx: CommandContext) -> str | None:
        agent = _require_agent(ctx)
        if agent is None:
            return "Нет активного агента."
        if agent.is_streaming:
            return "Агент отвечает — дождитесь завершения запроса."
        if not agent.memory.tail:
            return "История пуста — сжимать нечего."
        agent.request_compaction()
        return "Контекст будет сжат при следующем сообщении."

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

    def _session(ctx: CommandContext) -> str | None:
        ctx.app.open_session_palette()
        return None

    def _exit(ctx: CommandContext) -> str | None:
        ctx.app.quit()
        return None

    registry.register(Command("help", "список всех команд", _help))
    registry.register(Command("new", "создать нового агента (вкладку)", _new, "[name]"))
    registry.register(Command("close", "закрыть активного агента", _close))
    registry.register(Command("name", "переименовать активного агента", _name, "<name>"))
    registry.register(
        Command(
            "model",
            "палитра выбора модели / выбрать provider:model",
            _model,
            "[provider:model]",
            lambda ctx, prefix: [m for m in _model_ids(ctx) if m.startswith(prefix)],
        )
    )
    registry.register(Command("temperature", "сменить температуру", _temperature, "<0..2>"))
    registry.register(Command("top-p", "сменить top_p", _top_p, "<0..1>"))
    registry.register(Command("max-tokens", "сменить max_tokens", _max_tokens, "<n>"))
    registry.register(
        Command("stop", "stop-sequences (разделитель , ; \"\" — очистить)", _stop, "<seq1,seq2>")
    )
    registry.register(Command("system", "показать / заменить системный промпт", _system, "[path]"))
    registry.register(Command("history", "показать историю диалога", _history))
    registry.register(Command("clear", "очистить историю сессии", _clear))
    registry.register(
        Command("compact", "сжать контекст при следующем сообщении", _compact)
    )
    registry.register(
        Command("export", "экспортировать сессию активного агента в jsonl", _export, "[file]")
    )
    registry.register(
        Command(
            "session",
            "палитра всех сессий; выбор загружает в активного агента",
            _session,
        )
    )
    registry.register(Command("exit", "выход (или Ctrl+Q)", _exit))
    return registry
