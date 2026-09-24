# my_agent — HTTP-бэкенд для Web UI: задание

Спецификация фронтенда — `WEB_UI.MD`; ядро — `my_agent.MD`. **Контракт бэкенда
описан в `WEB_UI.MD` §5 (нормативный): §5.1 REST, §5.2 SSE-события, §5.3 —
это про фронтенд, не реализовывать.** Это задание фиксирует, как именно
обернуть существующее Python-ядро `my_agent` в HTTP API.

## 1. Цель

Реализовать HTTP-бэкенд, полностью закрывающий контракт `WEB_UI.MD` §5, так,
чтобы фронтенд `web/` (уже готов) заработал с реальным ядром без моков:

- запуск: `uv run my-agent-web` (скрипт в `pyproject.toml`), слушает
  `127.0.0.1:8321` (порт настраивается `--port` / `MY_AGENT_PORT`);
- `GET /api/config` отдаёт конфиг без ключей; стриминг ответов — SSE;
- мульти-агентность: N агентов в одном процессе, параллельные стримы,
  восстановление после перезапуска бэкенда (SQLite, автосохранение);
- все ключи API остаются в бэкенде: ни один ответ их не содержит.

## 2. Стек и расположение

- **FastAPI + uvicorn + sse-starlette** — рекомендация `WEB_UI.MD` §1.
  Добавить в `pyproject.toml` (dependencies + `[project.scripts]
  my-agent-web = "my_agent.web_server:main"`), `uv sync`.
- Код — в новом пакете `my_agent/web_server/`:
  - `__init__.py`
  - `app.py` — фабрика приложения: CORS не нужен (дев-прокси Vite),
    роутор как в §5.1;
  - `state.py` — реестр агентов: `dict[agent_id, Agent]` + shared
    `LLMClient`, `ToolRegistry`, `Config`, `SessionStore` (одни инстансы
    на процесс, как в TUI);
  - `dto.py` — pydantic-модели ответов: `ConfigDTO`, `AgentDTO`,
    `MessageDTO`, `UsageDTO`, `SessionInfoDTO`, `CommandDTO`
    (поля — точно как в `WEB_UI.MD` §5.1);
  - `stream.py` — SSE-обёртка над `Agent.start_ask()` (см. ниже).
- Префикс и статусы: ошибки всегда `{"detail": string}` со статусами
  400/401/404/409 (409 — «агент уже стримит» при `POST messages` и
  `DELETE /api/agents/{id}` во время хода).

## 3. Маппинг ядра на эндпоинты

Ядро уже умеет всё нужное — только оборачивать:

| Эндпоинт | Реализация |
|---|---|
| `GET /api/config` | `load_config()` → DTO без `api_key`; модели: `{id: {context_window}}` через `Config.providers` |
| `GET /api/commands` | `default_registry().all()` → `[{name, description, args_spec}]` |
| `GET/PUT /api/system-prompt` | `PUT {path}` → `agent.set_system_prompt_file(path)` (см. ниже — к какому агенту применяется) |
| `GET /api/agents` | реестр → `[AgentDTO]` |
| `POST /api/agents` | `Agent(name, AgentSettings.from_config(config), system_prompt=SYSTEM_PROMPT.md, llm=shared, config=shared)` → реестр |
| `DELETE /api/agents/{id}` | 409, если `agent.is_streaming`; иначе удалить из реестра |
| `PATCH /api/agents/{id}` | `name`, `model` (валидация `Config.resolve_model` → `ValueError` → 400), `temperature`, `top_p`, `max_tokens`, `stop`, `system_prompt_path` (→ `set_system_prompt_file`) |
| `GET /api/agents/{id}/messages` | `agent.memory.history` → `[MessageDTO]` |
| `DELETE /api/agents/{id}/messages` | `memory.clear()`; обнулить `agent.totals` и `last_usage` |
| `GET /api/sessions` | `SessionStore.list()` → `[{id, title, updated_at}]` |
| `POST load-session` | `store.get(session_id)` → `agent.apply_session(data)` → `AgentDTO` |
| `POST export` | `agent.export(path?)` → `{"path": ...}` |
| `POST messages` | SSE-поток, см. §3.1 |
| `POST cancel` | `agent.cancel_ask()` |

### 3.1 Стрим: `POST /api/agents/{id}/messages` → SSE

Ядро даёт `start_ask(text) -> asyncio.Task[str]` и публичное состояние
(`is_streaming`, `streaming_text()`, `streaming_out_estimate()`,
`has_first_response()`), TUI поллит их таймером (`my_agent/ui/app.py`,
`_tick`). В HTTP-версии источником событий служит **поллинг состояния агента**
из фонового писателя:

1. валидировать `{content}`; `AgentBusyError` → 409;
2. сначала отправить событие `user_message` (MessageDTO пользовательского
   сообщения — ядро добавит его в историю сам внутри `ask()`, но фронтенду
   оно нужно сразу);
3. `task = agent.start_ask(content)`; отвечать `text/event-stream`;
4. фоновая задача-«насос» (одна на запрос): каждые ~80–100 мс опрашивает
   агент и эмитит дельты:
   - `delta {content}` — новая часть `streaming_text()` (сравнение с ранее
     отправленным префиксом; ядро копит текст в `Agent._stream_text`);
   - `compaction_started` когда `agent.is_compacting` впервые стал True,
     `compaction_done {removed, summary_tokens, pct_before, pct_after}` из
     `agent.compaction_note`/`on_compaction` (подписку `on_compaction` можно
     выставить при создании агента: колбэк кладёт заметку в очередь);
   - `done {message: MessageDTO}` после завершения `task`: последнее
     assistant-сообщение истории + `usage` из `agent.last_usage`
     (`prompt_tokens`, `completion_tokens`, `reasoning_tokens`,
     `approx: usage.estimated`);
   - `cancelled` если `task.cancelled()` (после `cancel_ask()`);
   - `error {kind: "http"|"network", detail}` — `LLMError` со `status`
     4xx → `kind:"http"`, прочее (сетевое, таймаут) → `kind:"network"`;
     detail — строка исключения;
   - `subagent_* {profile[, content]}` — активность делегирования: насос
     дренит `agent.subagent_events` (`started` → `subagent_started`,
     `delta` → `subagent_delta`, `done` → `subagent_done`) и эмитит их между
     `tool_message`-событиями, не трогая основной `delta`-счёт.
5. между агентами стримы независимы: каждый `POST messages` живёт в своей
   задаче; `streaming` в `AgentDTO` — `agent.is_streaming`.

Прерывание клиента (закрытие SSE-соединения) НЕ отменяет ход агента — ход
продолжает исполняться ядром, автосохранение фиксирует результат.

### 3.2 Автосохранение и восстановление
- `SessionStore` (SQLite, `sessions/`): как в TUI — снапшот по тику
  (~2 с) и сразу после завершения хода; см. `my_agent/ui/app.py`
  (`_tick`, `_persist_tab`, fingerprint).
- При старте сервера загрузить из SQLite всех сохранённых агентов и
  восстановить (`apply_session`), чтобы `GET /api/agents` после перезапуска
  бэкенда вернул агентов с историями (критерий `WEB_UI.MD` §11: восстановление
  после перезагрузки страницы).
- `GET /api/system-prompt` / `PUT /api/system-prompt` работают с активным
  агентом: сервер хранит `active_agent_id` (первый агент по умолчанию);
  фронтенд передаёт активного явно НЕ будет — см. `WEB_UI.MD` §5.1: сервер
  сам держит last-active. `PATCH /api/agents/{id}` с `system_prompt_path`
  меняет промпт конкретного агента (`set_system_prompt_file`).

## 4. DTO-соответствия (сверить с фронтендом `web/src/api/types.ts`)

- `AgentDTO`: `{id, name, model, settings:{temperature, top_p, max_tokens,
  stop, context_strategy, sliding_window, compaction_threshold},
  system_prompt:{path, content}, context_used, context_window, streaming,
  compacting}`.
  - `context_used` — `agent.context_now[0]` (property, кортеж `(токены, оценка)`);
  - `context_window` — `agent.context_window`;
  - `compacting` — `agent.is_compacting or agent.is_extracting_facts`;
  - `system_prompt.path` — имя файла промпта (агент хранит только текст;
    путь держит сервер, для дефолта — `SYSTEM_PROMPT.md`).
- `MessageDTO`: `{id, role, content, usage?, error?}`. История ядра —
  `Message` (role/content/reasoning/...); id генерирует сервер (`m1`, `m2`,
  ... по индексу). `reasoning` в DTO не отдаётся. `error` — для
  system-сообщений об ошибках (сервер добавляет их в историю как
  `role="system"` при `LLMError` — так же, как TUI: `add_note`).
- `UsageDTO`: `{prompt_tokens?, completion_tokens?, reasoning_tokens?,
  approx?}` из `agent.last_usage` (`estimated` → `approx`).
- `ConfigDTO`: провайдеры `{name: {api_base, models: {id: {context_window}}}}`,
  `default_model`, дефолты настроек, `compaction_threshold`,
  `sliding_window`. **Без `api_key`** — тест обязателен.

## 5. Тесты (`tests/test_web_server.py`, pytest + httpx ASGI-клиент)

Ядро тестируется против LLM — тоже мокается (см. существующие тесты
`tests/`); стрим подменяется фейковым `LLMClient`. Обязательные кейсы:

- `GET /api/config` — 200, без `api_key` (строка «api_key» не встречается в теле);
- `GET /api/commands` — список содержит `close`, `export`;
- `POST /api/agents` → 200 AgentDTO; `PATCH` неизвестной модели → 400;
  `DELETE` несуществующего → 404; повторный `POST messages` во время стрима → 409;
- SSE: дельты склеиваются в `done.message.content == текст ответа мока`;
  `user_message` приходит первым; `cancelled` после `POST cancel`;
- делегирование: `delegate` создаёт эфемерного субагента (системный промпт =
  содержимое профиля), возвращает его ответ как `tool`-сообщение; субагент
  создаётся с `tools=None` (рекурсия невозможна); SSE-поток эмитит
  `subagent_started` → `subagent_delta` → `subagent_done`;
- задача (автомат): план из двух списков `steps` (выполнение) и
  `validation_steps` (проверка); `confirm_plan` переводит `planning → execution`
  (`plan_confirmed: true`); подтверждение вне `planning` или при пустом плане →
  `400`; переход `planning → execution` без подтверждения → `409`; возврат
  `validation → planning`; `task_advance_step(done=true)` явно завершает шаг и
  сбрасывает `step` на 1 при смене группы списка; `task_update_plan` заменяет
  план и сбрасывает `plan_confirmed`;
- `DELETE /api/agents/{id}/messages` → история пуста;
- восстановление: создать агента, снапшот, пересоздать `state`, `GET /api/agents`
  возвращает агента с историей.

## 6. Критерии готовности

- `uv run my-agent-web` поднимает бэкенд на `127.0.0.1:8321`;
  `cd web && npm run dev` подключается, сквозной сценарий §11 `WEB_UI.MD`
  проходит с реальным ядром (создание агента, стриминг, команды, палитры,
  восстановление после перезагрузки).
- `uv run ruff check .`, `uv run mypy my_agent` (строгий; типы обязательны),
  `uv run pytest` — зелёные.
- `api_key` не появляется ни в одном ответе (тест на `/api/config`).
