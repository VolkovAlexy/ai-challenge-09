# Задача: реализовать CLI-агент my_agent (v1)

Создай CLI-приложение `my_agent` — TUI-чат с LLM по OpenAI-compatible API,
с мульти-агентностью (несколько чатов-вкладок в одном процессе) и slash-командами
для настройки. Полная спецификация — в файле `my_agent.MD` в корне репозитория,
следуй ей строго; ниже — ключевые требования и критерии приёмки.

## Стек (обязательно)
- Python 3.12+, менеджер `uv` (pyproject.toml, `uv add`), запуск `uv run my-agent`
  через `[project.scripts] my-agent = "my_agent.cli:main"`.
- TUI: `textual` (вкладки, bindings, async workers).
- LLM: собственный тонкий клиент на `httpx` (AsyncClient) + `httpx-sse` —
  POST `{api_base}/chat/completions`, стриминг SSE, ретраи. Никаких openai-SDK.
- Валидация: `pydantic` v2. CLI-аргументы: `typer`.
- QA: `ruff`, `mypy` (строгий режим для `my_agent/`), `pytest`.

## Структура (строго по spec, раздел 3)
Пакет `my_agent/` с модулями: `cli.py`, `config/` (schema.py, store.py),
`core/` (agent.py, context.py, message.py), `llm/client.py`, `commands/registry.py`,
`memory/` (session.py, longterm.py), `tools/` (registry.py, mcp.py),
`ui/app.py` + `ui/widgets/` (chat_input, message_list, status_bar, help_palette).
В корне: `SYSTEM_PROMPT.md`, `config.json` (в .gitignore), `sessions/`.

## Ключевые архитектурные правила
1. `Agent` — класс, инстанс на каждый чат: свои настройки (клон дефолтов config
   + runtime-override), свой системный промпт, свой `SessionMemory`, id модели
   `provider:model`. Общие ресурсы (LLMClient, ToolRegistry) — шаред, в конструктор.
2. Состояние вне UI: вся логика в plain-Python классах, Textual — тонкий рендер.
   Стриминг в неактивной вкладке продолжается; при переключении вывод догоняется.
3. Конфиг — multi-provider (пример и правила — spec §5):
   `providers: {name: {api_base, api_key, models}}`, `default_model: "provider:model"`.
   pydantic валидирует, что default_model существует; `api_key` может быть пустым
   (ollama). Список моделей только из config.
4. При `ask()` агент резолвит провайдера из id модели → `api_base`/`api_key`
   запроса. `LLMClient` общий, параметры коннекта — per-запрос.
5. `ContextBuilder.build_messages(system_prompt, history, rag_chunks=None,
   memories=None)` — единственная точка, где собирается массив messages.
6. Модели `Message` с первого дня включают `tool_calls`/`tool` роли; LLM-клиент
   умеет парсить tool_calls в стриме (исполнения пока нет — ToolRegistry пуст).
7. Команды — реестр данных {name, description, handler, args_spec}: из него
   строятся /help, tab-completion. Команды настройки применяются к активному агенту.
8. Заделы (только протоколы + stub'ы с docstring, реализация НЕ писать):
   `LongTermMemory` (recall/store), `ToolRegistry` (register/get/all),
   `McpAdapter` (под stdio/streamable-http), `KnowledgeBase.search`.

## Команды (полный список — spec §6)
/help, /new [name], /close, /name <name>, /model [provider:model], /temperature,
/top-p, /max-tokens, /stop, /system [path], /history, /clear, /save [file],
/load <file>, /exit. Tab-completion по командам и моделям. Незначимый ввод → /help-подсказка.

## Поведение
- Стриминг: батч перерисовки MessageList ~100 мс; один активный запрос на агента.
- Ретраи: 3× (1s/2s/4s) только на 429/5xx/сетевые ошибки; 400/401 — ошибка в чате, чат продолжается.
- Ctrl+C: 1-й раз — отмена запроса активного агента, 2-й — выход. Ctrl+Q — выход.
- Вкладки: Ctrl+Tab/Ctrl+Shift+Tab, имя+модель на вкладке, индикатор стриминга.
- Системный промпт: ./SYSTEM_PROMPT.md по умолчанию, --system-prompt <path> для
  стартового агента, /system [path] в сессии. Нет файла → понятная ошибка, старт не начинается.
- /save → jsonl (sessions/<ts>.jsonl по умолчанию): история + настройки агента;
  /load восстанавливает в активного агента.

## Порядок реализации
По spec §9 (каркас → config → llm-клиент → core без UI → Textual UI → команды
→ мульти-агентность → save/load → заделы → README/.gitignore).

## Критерии приёмки (spec §10)
- `uv run my-agent` стартует, стриминг работает.
- Все команды работают; /new создаёт второго агента; параллельный стриминг в двух
  вкладках без потери вывода; смена /model между провайдерами (openai ↔ ollama)
  в одном чате; StatusBar показывает provider:model.
- --system-prompt переопределяет промпт; /save+/load восстанавливают сессию.
- `ruff check .`, `mypy my_agent`, `pytest` — зелёные. Напиши pytest-тесты на
  config (валидация), context builder, agent.ask (с мок-клиентом LLM),
  session save/load.
