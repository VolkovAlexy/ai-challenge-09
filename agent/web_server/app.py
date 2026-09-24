"""Фабрика FastAPI-приложения web-бэкенда и точка входа my-agent-web.

Эндпоинты в 1:1 соответствие маршрутам web/src/api/client.ts. Ключи API
остаются в backend — ни один ответ их не содержит. Ошибки — {"detail": str}
с 400/404/409 (409 = «агент уже отвечает»).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from sse_starlette.sse import EventSourceResponse

from agent.commands.registry import default_registry
from agent.config.store import DEFAULT_CONFIG_PATH, load_config
from agent.core.agent import AgentBusyError
from agent.core.task import InvalidTaskTransition
from agent.llm.client import LLMClient
from agent.memory.persistence import SessionStore
from agent.tools.mcp_manager import McpManager
from agent.tools.registry import ToolRegistry
from agent.web_server import dto
from agent.web_server.state import (
    DEFAULT_SYSTEM_PROMPT_PATH,
    AgentRecord,
    WebState,
)
from agent.web_server.stream import agent_stream
from agent.web_server.watchdog import watchdog_loop

AUTOSAVE_INTERVAL = 2.0
SESSIONS_DIR = Path("sessions")


def _sessions_db() -> Path:
    return SESSIONS_DIR / "sessions.db"


async def _autosave_loop(state: WebState, interval: float) -> None:
    """Период-тик автосохранения: снапшот только «грязных» агентов."""
    while True:
        await asyncio.sleep(interval)
        state.persist_if_dirty()


def create_app(state: WebState) -> FastAPI:
    """Собирает приложение поверх готового WebState (регия, ресурсы, store)."""

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        tick = asyncio.create_task(_autosave_loop(state, AUTOSAVE_INTERVAL))
        probe = asyncio.create_task(state.start_mcp())
        watchdog: asyncio.Task[None] | None = None
        if state.config.watchdog is not None and state.config.watchdog.enabled:
            watchdog = asyncio.create_task(
                watchdog_loop(state, state.config.watchdog),
                name="watchdog",
            )
        try:
            yield
        finally:
            tick.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tick
            probe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe
            if watchdog is not None:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog
            if state.mcp is not None:
                await state.mcp.stop()
            await state.llm.close()
            state.store.close()

    app = FastAPI(title="my-agent web backend", lifespan=lifespan)

    def _record_or_404(agent_id: str) -> AgentRecord:
        record = state.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="агент не найден")
        return record

    def _active_or_404() -> AgentRecord:
        record = state.active_agent()
        if record is None:
            raise HTTPException(status_code=404, detail="нет активного агента")
        return record

    # --- конфиг / команды ---

    @app.get("/api/config")
    def get_config() -> dto.ConfigDTO:
        return state.config_dto()

    @app.get("/api/commands")
    def get_commands() -> list[dto.CommandDTO]:
        return state.commands_dto(default_registry())

    # --- MCP-серверы ---

    @app.get("/api/mcp")
    async def get_mcp() -> list[dto.McpDTO]:
        return state.list_mcp()

    @app.patch("/api/mcp/{name}")
    async def patch_mcp(name: str, body: dto.McpPatchRequest) -> list[dto.McpDTO]:
        try:
            return await state.set_mcp_enabled(name, body.enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --- системный промпт (активный агент) ---

    @app.get("/api/system-prompt")
    def get_system_prompt() -> dto.SystemPromptDTO:
        record = _active_or_404()
        return dto.SystemPromptDTO(
            path=record.system_prompt_path,
            content=record.agent.system_prompt,
        )

    @app.put("/api/system-prompt")
    def put_system_prompt(body: dto.SystemPromptPutRequest) -> dto.SystemPromptDTO:
        record = _active_or_404()
        try:
            record.agent.set_system_prompt_file(body.path)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record.system_prompt_path = body.path
        state.persist(record)
        return dto.SystemPromptDTO(
            path=record.system_prompt_path,
            content=record.agent.system_prompt,
        )

    # --- агенты ---

    @app.get("/api/agents")
    def list_agents() -> list[dto.AgentDTO]:
        return [state.agent_dto(record) for record in state.records.values()]

    @app.post("/api/agents")
    def create_agent(body: dto.CreateAgentRequest | None = None) -> dto.AgentDTO:
        name = body.name if body is not None else None
        project_id = body.project_id if body is not None else None
        record = state.create_agent(name, project_id)
        state.active_agent_id = record.agent_id
        return state.agent_dto(record)

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str) -> dict[str, bool]:
        record = state.get(agent_id)
        if record is None:
            raise HTTPException(status_code=404, detail="агент не найден")
        if record.agent.is_streaming:
            raise HTTPException(status_code=409, detail="агент уже отвечает")
        state.delete_agent(agent_id)
        return {"ok": True}

    @app.patch("/api/agents/{agent_id}")
    def patch_agent(agent_id: str, body: dto.PatchAgentRequest) -> dto.AgentDTO:
        record = _record_or_404(agent_id)
        agent = record.agent
        if body.name is not None:
            agent.rename(body.name)
        if body.model is not None:
            try:
                agent.set_model(body.model)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        if body.temperature is not None:
            agent.settings.temperature = body.temperature
        if body.top_p is not None:
            agent.settings.top_p = body.top_p
        if body.max_tokens is not None:
            agent.settings.max_tokens = body.max_tokens
        if body.stop is not None:
            agent.settings.stop = list(body.stop)
        if body.context_strategy is not None:
            agent.settings.context_strategy = body.context_strategy
        if body.sliding_window is not None:
            agent.settings.sliding_window = body.sliding_window
        if body.compaction_threshold is not None:
            agent.settings.compaction_threshold = body.compaction_threshold
        if body.system_prompt_path is not None:
            try:
                agent.set_system_prompt_file(body.system_prompt_path)
            except OSError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            record.system_prompt_path = body.system_prompt_path
        if body.active_profile_id is not None:
            try:
                state.set_active_profile(record.agent_id, body.active_profile_id)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        state.set_active(agent_id)
        state.persist(record)
        return state.agent_dto(record)

    # --- история ---

    @app.get("/api/agents/{agent_id}/messages")
    def get_messages(agent_id: str) -> list[dto.MessageDTO]:
        record = _record_or_404(agent_id)
        return state.history_dto(record)

    @app.delete("/api/agents/{agent_id}/messages")
    def clear_messages(agent_id: str) -> dict[str, bool]:
        record = _record_or_404(agent_id)
        agent = record.agent
        agent.memory.clear()
        agent.reset_totals()
        agent.last_usage = None
        agent.message_usage = {}
        state.persist(record)
        return {"ok": True}

    # --- сессии ---

    @app.get("/api/sessions")
    def list_sessions(
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[dto.SessionInfoDTO]:
        return state.sessions_dto(limit=limit, offset=offset, project_id=project_id)

    @app.post("/api/agents/{agent_id}/load-session")
    def load_session(agent_id: str, body: dto.LoadSessionRequest) -> dto.AgentDTO:
        try:
            record = state.load_session(agent_id, body.session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return state.agent_dto(record)

    @app.post("/api/agents/{agent_id}/export")
    def export_session(agent_id: str, body: dto.ExportRequest | None = None) -> dict[str, str]:
        record = _record_or_404(agent_id)
        if body is not None and body.path:
            target: str | Path = body.path
        else:
            timestamp = Path(_now_stamp())
            target = SESSIONS_DIR / f"{timestamp}.jsonl"
        saved = record.agent.export(target)
        return {"path": str(saved)}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, bool]:
        if not state.delete_session(session_id):
            raise HTTPException(status_code=404, detail="сессия не найдена")
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/branch")
    def branch_session(session_id: str) -> dto.AgentDTO:
        try:
            record = state.branch_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return state.agent_dto(record)

    @app.patch("/api/sessions/{session_id}")
    def patch_session(session_id: str, body: dto.SessionPatchRequest) -> dict[str, bool]:
        if not state.store.set_title(session_id, body.title):
            raise HTTPException(status_code=404, detail="сессия не найдена")
        return {"ok": True}

    @app.post("/api/scheduler/notify")
    def scheduler_notify(body: dto.SchedulerEventRequest) -> dict[str, bool]:
        state.handle_scheduler_event(body)
        return {"ok": True}

    @app.post("/api/scheduler/mark-read")
    def scheduler_mark_read(session_id: str) -> dict[str, bool]:
        state.mark_session_read(session_id)
        return {"ok": True}

    # --- управление ходом ---

    @app.post("/api/agents/{agent_id}/cancel")
    def cancel_ask(agent_id: str) -> dict[str, bool]:
        record = _record_or_404(agent_id)
        cancelled = record.agent.cancel_ask()
        return {"cancelled": cancelled}

    @app.post("/api/agents/{agent_id}/messages")
    def send_message(
        agent_id: str, body: dto.SendMessageRequest
    ) -> EventSourceResponse:
        record = _record_or_404(agent_id)
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="content не может быть пустым")
        if record.agent.is_streaming:
            raise HTTPException(status_code=409, detail="агент уже отвечает")
        state.set_active(agent_id)
        return EventSourceResponse(agent_stream(state, record, body.content))

    # --- рабочая память (scratchpad) ---

    @app.put("/api/agents/{agent_id}/scratchpad")
    def put_scratchpad(agent_id: str, body: dto.ScratchpadPutRequest) -> dict[str, str]:
        record = _record_or_404(agent_id)
        record.agent.memory.scratchpad = body.content
        state.persist(record)
        return {"content": record.agent.memory.scratchpad}

    # --- facts (стратегия фактов) ---

    @app.get("/api/agents/{agent_id}/facts")
    def get_facts(agent_id: str) -> dict[str, str]:
        record = _record_or_404(agent_id)
        return dict(record.agent.memory.facts)

    @app.put("/api/agents/{agent_id}/facts")
    def put_facts(agent_id: str, body: dto.FactsPutRequest) -> dict[str, str]:
        record = _record_or_404(agent_id)
        record.agent.memory.facts = dict(body.facts)
        state.persist(record)
        return dict(record.agent.memory.facts)

    # --- состояние задачи (конечный автомат) ---

    @app.post("/api/agents/{agent_id}/task")
    def post_task(agent_id: str, body: dto.TaskCommandRequest) -> dto.AgentDTO:
        _record_or_404(agent_id)
        try:
            record = state.apply_task_command(agent_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except InvalidTaskTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return state.agent_dto(record)

    # --- ветвление от сообщения ---

    @app.post("/api/agents/{agent_id}/fork")
    def fork_at(agent_id: str, body: dto.ForkRequest) -> dict[str, object]:
        try:
            record = state.fork_at(agent_id, body.message_index)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AgentBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        state.set_active(agent_id)
        return {
            "agent": state.agent_dto(record).model_dump(),
            "messages": [message.model_dump() for message in state.history_dto(record)],
        }

    # --- долговременная память (по проекту) ---

    @app.get("/api/longterm")
    def get_longterm(project_id: str | None = None) -> dto.LongTermDTO:
        return state.longterm_dto(project_id)

    @app.post("/api/longterm")
    def post_longterm(body: dto.RememberRequest, project_id: str | None = None) -> dto.LongTermDTO:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="content не может быть пустым")
        return state.remember(body.content, project_id)

    @app.delete("/api/longterm/{index}")
    def delete_longterm(index: int, project_id: str | None = None) -> dto.LongTermDTO:
        try:
            return state.forget(index, project_id)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/longterm/{index}")
    def put_longterm(
        index: int, body: dto.RememberRequest, project_id: str | None = None
    ) -> dto.LongTermDTO:
        if not body.content.strip():
            raise HTTPException(status_code=400, detail="content не может быть пустым")
        try:
            return state.update_longterm(index, body.content, project_id)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # --- проекты (Слой 1) ---

    @app.get("/api/projects")
    def list_projects() -> list[dto.ProjectDTO]:
        return state.list_projects()

    @app.post("/api/projects")
    def create_project(body: dto.CreateProjectRequest) -> dto.ProjectDTO:
        try:
            return state.create_project(body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dto.ProjectDTO:
        project = state.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="проект не найден")
        return project

    @app.patch("/api/projects/{project_id}")
    def patch_project(project_id: str, body: dto.PatchProjectRequest) -> dto.ProjectDTO:
        if not state.rename_project(project_id, body.name):
            raise HTTPException(status_code=404, detail="проект не найден")
        project = state.get_project(project_id)
        assert project is not None
        return project

    @app.delete("/api/projects/{project_id}")
    def delete_project(project_id: str) -> dict[str, bool]:
        if project_id == state.default_project_id:
            raise HTTPException(status_code=400, detail="нельзя удалить проект по умолчанию")
        if not state.delete_project(project_id):
            raise HTTPException(status_code=404, detail="проект не найден")
        return {"ok": True}

    # --- профили (глобальный пул + привязка к проекту) ---

    @app.get("/api/profiles")
    def list_profiles() -> list[dto.ProfileDTO]:
        return state.list_profiles()

    @app.post("/api/profiles")
    def create_profile(body: dto.ProfileRequest) -> dto.ProfileDTO:
        try:
            return state.create_profile(body.name, body.content)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/profiles/{profile_id}")
    def get_profile(profile_id: str) -> dto.ProfileDTO:
        profile = state.get_profile(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="профиль не найден")
        return profile

    @app.patch("/api/profiles/{profile_id}")
    def patch_profile(profile_id: str, body: dto.PatchProfileRequest) -> dto.ProfileDTO:
        existing = state.get_profile(profile_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="профиль не найден")
        name = body.name if body.name is not None else existing.name
        content = body.content if body.content is not None else existing.content
        try:
            return state.update_profile(profile_id, name, content)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/profiles/{profile_id}")
    def delete_profile(profile_id: str) -> dict[str, bool]:
        if not state.delete_profile(profile_id):
            raise HTTPException(status_code=404, detail="профиль не найден")
        return {"ok": True}

    @app.get("/api/projects/{project_id}/profiles")
    def get_project_profiles(project_id: str) -> list[dto.ProfileDTO]:
        try:
            return state.project_profiles_dto(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.put("/api/projects/{project_id}/profiles")
    def put_project_profiles(
        project_id: str, body: dto.ProjectProfilesRequest
    ) -> list[dto.ProfileDTO]:
        try:
            return state.set_project_profile_ids(project_id, body.profile_ids)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- предложение памяти (MEMORY_SUGGESTION) ---

    @app.post("/api/agents/{agent_id}/memory-suggestion/accept")
    def accept_memory_suggestion(agent_id: str) -> dto.LongTermDTO:
        try:
            return state.accept_suggestion(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/agents/{agent_id}/memory-suggestion/dismiss")
    def dismiss_memory_suggestion(agent_id: str) -> dict[str, bool]:
        record = _record_or_404(agent_id)
        record.agent.dismiss_suggestion()
        return {"ok": True}

    return app


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def main() -> None:
    """Точка входа `uv run my-agent-web`: поднимает uvicorn на 127.0.0.1:8321."""
    parser = argparse.ArgumentParser(prog="agent-web", description="web-бэкенд agent")
    parser.add_argument(
        "--host", default="127.0.0.1", help="адрес для прослушивания"
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MY_AGENT_PORT", "8321")),
        help="порт (env MY_AGENT_PORT)",
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("MY_AGENT_CONFIG", str(DEFAULT_CONFIG_PATH)),
        help="путь к config.json",
    )
    parser.add_argument("--sessions", default=None, help="путь к sessions.db")
    parser.add_argument(
        "--reload", action="store_true",
        help="горячая перезагрузка кода (dev-режим)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    llm = LLMClient()
    tools = ToolRegistry()
    mcp = McpManager(config.mcp_servers)
    store = SessionStore(args.sessions or _sessions_db())
    prompt_path = Path(DEFAULT_SYSTEM_PROMPT_PATH)
    prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
    state = WebState(
        config=config,
        llm=llm,
        tools=tools,
        store=store,
        default_system_prompt=prompt,
        default_prompt_path=DEFAULT_SYSTEM_PROMPT_PATH,
        mcp=mcp,
    )
    app = create_app(state)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
