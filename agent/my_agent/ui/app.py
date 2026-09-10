"""Textual App: вкладка = агент. Тонкий слой рендеринга над plain-Python состоянием.

Стриминг в неактивной вкладке продолжается (задача Agent.ask живёт в asyncio);
при переключении вкладка догоняет вывод из состояния агента. Перерисовка
активной вкладки батчится таймером ~100 мс.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import TabbedContent, TabPane

from my_agent.commands.registry import CommandContext, CommandRegistry, default_registry
from my_agent.config.schema import AgentSettings, Config
from my_agent.core.agent import Agent, AgentBusyError
from my_agent.llm.client import LLMClient, LLMError
from my_agent.memory.persistence import SessionStore
from my_agent.tools.registry import ToolRegistry
from my_agent.ui.widgets.chat_input import ChatInput
from my_agent.ui.widgets.help_palette import HelpPalette
from my_agent.ui.widgets.message_list import MessageList
from my_agent.ui.widgets.model_palette import ModelPalette
from my_agent.ui.widgets.session_palette import SessionPalette
from my_agent.ui.widgets.status_bar import StatusBar

TICK_INTERVAL = 0.1  # ~100 мс — батч перерисовки
CTRL_C_WINDOW = 3.0  # окно «повторный Ctrl+C — выход»


@dataclass
class ChatTab:
    """Состояние вкладки вне Textual-виджетов: агент + заметки (вывод команд/ошибки).

    `session_id` — id в SessionStore; `_fingerprint` — след состояния для
    автосохранения (тик сравнивает и пересохраняет изменившуюся вкладку).
    """

    agent: Agent
    notes: list[tuple[str, str]] = field(default_factory=list)
    dirty: bool = True
    session_id: str | None = None
    _fingerprint: tuple[object, ...] = field(default_factory=tuple)

    def add_note(self, kind: str, text: str) -> None:
        self.notes.append((kind, text))
        self.dirty = True


class AgentView(Vertical):
    """Одна вкладка: список сообщений, статус-бар, строка ввода."""

    def __init__(self, tab: ChatTab) -> None:
        super().__init__(id=f"agent-{id(tab.agent)}")
        self.tab = tab
        self.messages = MessageList(tab)
        self.status = StatusBar(tab)
        self.input = ChatInput(tab)

    def compose(self) -> Iterator[Widget]:
        yield self.messages
        yield self.status
        yield self.input

    def refresh_all(self) -> None:
        self.messages.refresh_follow()
        self.status.refresh()

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        app = self.app
        if isinstance(app, AgentApp) and app.handle_input(self.tab, event.value):
            event.input.clear()


class AgentApp(App[None]):
    TITLE = "my_agent"
    CSS = """
    TabbedContent {
        height: 1fr;
    }
    TabPane {
        height: 1fr;
    }
    AgentView {
        width: 100%;
        height: 100%;
    }
    """

    BINDINGS = [  # noqa: RUF012
        Binding("ctrl+q", "quit", "Exit"),
        Binding("ctrl+c", "cancel_or_quit", "Cancel/Exit"),
        Binding("ctrl+tab", "next_tab", "Next tab"),
        Binding("ctrl+shift+tab", "previous_tab", "Prev tab"),
        Binding("f1", "open_help", "Help"),
    ]

    def __init__(
        self,
        *,
        config: Config,
        llm: LLMClient,
        tools: ToolRegistry,
        default_system_prompt: str,
        startup_system_prompt: str | None = None,
        startup_model: str | None = None,
        startup_name: str = "chat-1",
        session_db: Path | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._llm = llm
        self._tools = tools
        self._commands = default_registry()
        self._default_system_prompt = default_system_prompt
        self._startup_system_prompt = startup_system_prompt or default_system_prompt
        self._startup_model = startup_model
        self._startup_name = startup_name
        self._store = SessionStore(session_db or Path("sessions") / "sessions.db")
        self._tabs: list[ChatTab] = []
        self._tab_ids: dict[int, str] = {}
        self._views: dict[int, AgentView] = {}
        self._labels: dict[str, str] = {}
        self._last_ctrl_c: float = 0.0

    # --- compose ---

    def compose(self) -> Iterator[Widget]:
        yield TabbedContent()

    async def on_mount(self) -> None:
        self.add_agent(
            self._startup_name,
            model=self._startup_model,
            system_prompt=self._startup_system_prompt,
        )
        self.set_interval(TICK_INTERVAL, self._tick)

    async def on_unmount(self) -> None:
        """Финальный flush всех вкладок в store + закрытие подключения."""
        try:
            self._persist_all()
        finally:
            self._store.close()

    # --- AppLike (команды) ---

    def config(self) -> Config:
        return self._config

    def agent_count(self) -> int:
        return len(self._tabs)

    def active_tab(self) -> ChatTab | None:
        tabbed = self.query_one(TabbedContent)
        active_id = tabbed.active
        for tab in self._tabs:
            if self._tab_ids[id(tab.agent)] == active_id:
                return tab
        return self._tabs[0] if self._tabs else None

    def active_agent(self) -> Agent | None:
        tab = self.active_tab()
        return tab.agent if tab else None

    def add_agent(
        self, name: str, *, model: str | None = None, system_prompt: str | None = None
    ) -> Agent:
        settings = AgentSettings.from_config(self._config)
        if model:
            settings.model = model
        prompt = system_prompt if system_prompt is not None else self._default_system_prompt
        agent = Agent(
            name=name,
            settings=settings,
            system_prompt=prompt,
            llm=self._llm,
            config=self._config,
            tools=self._tools,
        )
        tab = ChatTab(agent=agent)
        self._persist_tab(tab)  # выдаёт session_id + начальный (пустой) снапшот
        tab_id = f"agent-{id(agent)}"
        self._tabs.append(tab)
        self._tab_ids[id(agent)] = tab_id
        label = self._tab_label(tab)
        self._labels[tab_id] = label
        view = AgentView(tab)
        self._views[id(agent)] = view
        self.query_one(TabbedContent).add_pane(TabPane(label, view, id=tab_id))
        self.call_later(self._set_active_tab, tab_id)
        return agent

    def _set_active_tab(self, tab_id: str) -> None:
        tabbed = self.query_one(TabbedContent)
        if tabbed.active != tab_id:
            tabbed.active = tab_id

    def close_agent(self, agent: Agent) -> bool:
        tab = next((t for t in self._tabs if t.agent is agent), None)
        if tab is None:
            return False
        if len(self._tabs) == 1:
            self.exit()
            return True
        tab_id = self._tab_ids[id(agent)]
        self.query_one(TabbedContent).remove_pane(tab_id)
        self._tabs.remove(tab)
        del self._tab_ids[id(agent)]
        self._views.pop(id(agent), None)
        self._labels.pop(tab_id, None)
        return True

    def quit(self) -> None:
        self.exit()

    def open_help(self) -> None:
        entries = [(c.name, c.args_spec, c.description) for c in self._commands.all()]
        self.push_screen(HelpPalette("Команды", entries))

    def open_model_palette(self, tab: ChatTab) -> None:
        self.push_screen(
            ModelPalette(self._config.all_model_ids(), tab.agent.settings.model),
            lambda model_id: self._on_model_picked(tab, model_id),
        )

    def _on_model_picked(self, tab: ChatTab, model_id: str | None) -> None:
        if model_id is None:
            return
        try:
            tab.agent.set_model(model_id)
        except ValueError as exc:
            tab.add_note("error", f"Ошибка: {exc}")
            return
        tab.add_note("system", f"Модель: {model_id}")

    def open_session_palette(self) -> None:
        current = self.active_tab()
        current_id = current.session_id if current is not None else None
        self.push_screen(
            SessionPalette(self._store.list(), current_id),
            lambda session_id: self._on_session_picked(session_id),
        )

    def _on_session_picked(self, session_id: str | None) -> None:
        """Загружает выбранную сессию в активного агента (= продолжаем её)."""
        tab = self.active_tab()
        if session_id is None or tab is None:
            return
        if tab.session_id == session_id:
            tab.add_note("system", "Эта сессия уже открыта у активного агента.")
            return
        data = self._store.get(session_id)
        if data is None:
            tab.add_note("error", f"Сессия {session_id} не найдена.")
            return
        tab.agent.apply_session(data)
        tab.session_id = session_id
        tab.dirty = True
        self._persist_tab(tab)  # фиксируем fingerprint, чтобы не перезаписывать лишнее

    def complete_command(self, line: str) -> list[str]:
        """Tab-completion строки ввода (использует активный агент как контекст)."""
        tab = self.active_tab()
        ctx = CommandContext(app=self, agent=tab.agent if tab else None)
        return self._commands.complete(ctx, line)

    # --- ввод: чат и команды ---

    def handle_input(self, tab: ChatTab, text: str) -> bool:
        """Обработать ввод; True — строка расправлена (очищаем input)."""
        text = text.strip()
        if not text:
            return True
        if not text.startswith("/"):
            return self._start_ask(tab, text)
        parsed = CommandRegistry.parse_line(text)
        if parsed is None:
            tab.add_note("system", "Незначительный ввод. /help — список команд.")
            return True
        name, args = parsed
        if name == "help":
            self.open_help()
            return True
        if name == "model" and not args:
            self.open_model_palette(tab)
            return True
        if name == "session":
            self.open_session_palette()
            return True
        ctx = CommandContext(app=self, agent=tab.agent)
        output = self._commands.run(ctx, text)
        if output:
            tab.add_note("system", output)
        tab.dirty = True
        return True

    def _start_ask(self, tab: ChatTab, text: str) -> bool:
        agent = tab.agent
        if agent.is_streaming:
            tab.add_note("system", "Агент ещё отвечает — дождитесь или отмените запрос (Ctrl+C).")
            return False
        try:
            task = agent.start_ask(text)
        except AgentBusyError:
            tab.add_note("system", "Агент занят другим запросом.")
            return False
        tab.dirty = True
        task.add_done_callback(lambda t: self._on_ask_done(t, tab))
        return True

    def _on_ask_done(self, task: asyncio.Task[str], tab: ChatTab) -> None:
        tab.dirty = True
        try:
            task.result()
        except asyncio.CancelledError:
            tab.add_note("system", "Запрос отменён.")
        except LLMError as exc:
            tab.add_note("error", f"Ошибка LLM: {exc}")
        except Exception as exc:
            tab.add_note("error", f"Непредвиденная ошибка: {exc}")
        else:
            usage = tab.agent.last_usage
            if usage is not None and usage.truncated:
                tab.add_note(
                    "warning",
                    "⚠ Контекст переполнен: провайдер обработал меньше токенов, чем "
                    "отправлено, — часть истории модель не видела.",
                )
            if tab.agent.compaction_note:
                tab.add_note("system", tab.agent.compaction_note)
        self._persist_tab(tab)  # завершённый ход фиксируем сразу (не дожидаясь тика)

    # --- таймер: батч-перерисовка и метки вкладок ---

    def _tick(self) -> None:
        tab = self.active_tab()
        if tab is not None and (tab.dirty or tab.agent.is_streaming):
            self._view_of(tab).refresh_all()
            tab.dirty = False
        tabbed = self.query_one(TabbedContent)
        for tab in self._tabs:
            tab_id = self._tab_ids[id(tab.agent)]
            if self._tab_fingerprint(tab) != tab._fingerprint:
                self._persist_tab(tab)
            label = self._tab_label(tab)
            if self._labels.get(tab_id) != label:
                self._labels[tab_id] = label
                with contextlib.suppress(Exception):  # вкладка могла исчезнуть
                    tabbed.get_tab(tab_id).label = label

    def _tab_label(self, tab: ChatTab) -> str:
        agent = tab.agent
        label = f"{agent.name} · {agent.settings.model}"
        return label + " ▮" if agent.is_streaming else label

    def _view_of(self, tab: ChatTab) -> AgentView:
        return self._views[id(tab.agent)]

    # --- автосохранение сессий ---

    @staticmethod
    def _tab_fingerprint(tab: ChatTab) -> tuple[object, ...]:
        """Лёгкий след состояния вкладки: сравнение дешевле, чем запись."""
        agent = tab.agent
        history = agent.memory.history
        last = history[-1].to_api() if history else None
        settings = agent.settings
        return (
            len(history),
            last,
            agent.name,
            settings.model,
            settings.temperature,
            settings.top_p,
            settings.max_tokens,
            tuple(settings.stop),
            agent.system_prompt,
            agent.memory.summary,
        )

    def _persist_tab(self, tab: ChatTab) -> None:
        """Атомарный снапшот вкладки в store + фиксация fingerprint."""
        if tab.session_id is None:
            tab.session_id = self._store.new_id()
        agent = tab.agent
        self._store.snapshot(
            tab.session_id,
            agent.name,
            agent.settings,
            agent.system_prompt,
            agent.memory.history,
            summary=agent.memory.summary,
        )
        tab._fingerprint = self._tab_fingerprint(tab)

    def _persist_all(self) -> None:
        for tab in self._tabs:
            self._persist_tab(tab)

    # --- события вкладок ---

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        tab = self.active_tab()
        if tab is not None:
            tab.dirty = True  # догнать вывод из состояния агента
            self._view_of(tab).refresh_all()
            self._view_of(tab).input.focus()

    # --- клавиши ---

    def _cycle_tab(self, direction: int) -> None:
        ids = list(self._tab_ids.values())
        if not ids:
            return
        tabbed = self.query_one(TabbedContent)
        current = tabbed.active
        if current not in ids:
            tabbed.active = ids[0]
            return
        tabbed.active = ids[(ids.index(current) + direction) % len(ids)]

    def action_next_tab(self) -> None:
        self._cycle_tab(+1)

    def action_previous_tab(self) -> None:
        self._cycle_tab(-1)

    def action_open_help(self) -> None:
        self.open_help()

    def action_cancel_or_quit(self) -> None:
        now = time.monotonic()
        canceled = False
        tab = self.active_tab()
        if tab is not None:
            canceled = tab.agent.cancel_ask()
            if canceled:
                tab.add_note("system", "Запрос отменён.")
                tab.dirty = True
        if not canceled:
            if now - self._last_ctrl_c < CTRL_C_WINDOW:
                self.exit()
                return
            self._last_ctrl_c = now
