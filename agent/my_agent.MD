# my_agent — CLI-агент для работы с LLM

Спецификация CLI-чата на LLM с OpenAI-compatible API, настраиваемого через slash-команды,
с мульти-агентностью (несколько чатов в одном процессе),
с заделом под MCP, RAG и управление памятью.

## 1. Общие требования

- CLI-утилита, работает в режиме чата.
- Обращение к LLM по OpenAI-compatible API (`POST /chat/completions`, стриминг через SSE).
- Команды в формате `/<команда>` — настройка агента (модель, temperature, stop-sequences и т.д.).
- Системный промпт хранится в MD-файле в корне проекта (`SYSTEM_PROMPT.md`).
- Системный промпт можно переопределить при запуске: `--system-prompt <path>`.
- **Мульти-агентность в v1**: несколько чатов (инстансов агента) одновременно в одном процессе,
  переключение вкладками. Стриминг в неактивной вкладке продолжается.
- Архитектура предусматривает будущие расширения (не реализуются в v1):
  - подключение MCP (инструменты),
  - RAG (контекст из внешних источников по запросу),
  - управление памятью: сессионная и долгосрочная.

## 2. Стек

| Слой | Выбор | Обоснование |
|---|---|---|
| Runtime | Python 3.12+ | асинхронность (asyncio), лучшая LLM-экосистема |
| Менеджер | `uv` | `pyproject.toml`, `uv run`, `uv add`; быстрый, lock-файл |
| TUI | `textual` | полноценный TUI: вкладки, layout, bindings, workers для async |
| LLM-клиент | `httpx` (AsyncClient) + `httpx-sse` | собственный тонкий клиент поверх httpx: SSE-стриминг, ретраи; полный контроль, минимум зависимостей |
| Валидация | `pydantic` v2 | схема `config.json`, человекочитаемые ошибки |
| CLI-аргументы | `typer` | `--system-prompt <path>`, `--model`, `--config` |
| Тесты/качество | `pytest`, `ruff`, `mypy` | базовый QA-контур |

Запуск: `uv run my-agent` (консоль-скрипт, `[project.scripts]` в pyproject.toml).

## 3. Структура проекта

```
my_agent/
├── pyproject.toml            # uv-проект, [project.scripts] my-agent = "my_agent.cli:main"
├── SYSTEM_PROMPT.md          # системный промпт (по умолчанию)
├── config.json               # настройки (в .gitignore — содержит API key)
├── sessions/                 # файлы сессий для /save и /load (jsonl)
└── my_agent/
    ├── cli.py                # entry: typer, парсинг аргументов, запуск Textual App
    ├── config/
    │   ├── schema.py         # pydantic-модель Config, значения по умолчанию
    │   └── store.py          # load/save config.json
    ├── core/
    │   ├── agent.py          # класс Agent: ask(), настройки, промпт, сессия
    │   ├── context.py        # ContextBuilder: собирает массив messages
    │   └── message.py        # модели: Role, Message, ChatRequest, ChatChunk
    ├── llm/
    │   └── client.py         # OpenAI-compatible клиент: astream(), ретраи
    ├── commands/
    │   └── registry.py       # реестр команд {name, description, handler, apply_to_config?}
    ├── memory/
    │   ├── session.py        # InMemorySession: история, save() → jsonl, load()
    │   └── longterm.py       # [задел] протокол LongTermMemory (stub в v1)
    ├── tools/
    │   ├── registry.py       # [задел] ToolRegistry: {name, description, schema, execute}
    │   └── mcp.py            # [задел] MCP-адаптер (stub в v1)
    └── ui/
        ├── app.py            # Textual App: TabbedContent (вкладка = агент), bindings
        └── widgets/
            ├── chat_input.py     # ввод + tab-completion для /cmd
            ├── message_list.py   # история + стриминг токенов
            ├── status_bar.py     # модель, temperature, max_tokens, имя агента
            ├── help_palette.py   # список команд по /help
            └── model_palette.py  # палитра выбора модели (фильтр + OptionList) по /model
```

## 4. Ключевые архитектурные решения

1. **`Agent` — класс, инстанс на каждый чат.**
   Каждый `Agent`: свои настройки (клон из config.json + override), свой системный промпт,
   свой `SessionMemory`, своя история. Общие ресурсы — шаред-синглтоны, передаваемые в конструктор:
   `LLMClient` (httpx), `ToolRegistry`, `KnowledgeBase`, config-store.
   Мульти-агентность — это просто N инстансов `Agent`.

2. **Состояние вне UI (stores).**
   Вся логика и состояние — в plain-Python классах (`Agent`, `SessionMemory`),
   Textual-виджеты — тонкий слой рендеринга. Неактивная вкладка не рендерится,
   но её `Agent` продолжает асинхронно стримить в фоновом worker'е;
   при переключении вкладка догоняет вывод из состояния агента.

3. **ContextBuilder — главная точка расширения.**
   `build_messages(config, system_prompt, history, rag_chunks=None, memories=None) -> list[Message]`.
   Сейчас: системный промпт + история. В будущем: recall из long-term memory и RAG-чанки
   добавляются в этот модуль, `agent.py` не меняется.

4. **ToolRegistry с первого дня.**
   Модели `Message` уже включают `tool_calls`/`tool` роли; LLM-клиент умеет обрабатывать
   tool_calls в стриминговом ответе. Реестр пуст в v1, MCP-клиент в будущем регистрирует
   свои инструменты в тот же `ToolRegistry`.

5. **Команды как данные.**
   Реестр команд с метаданными даёт бесплатно: `/help`, tab-completion,
   сериализацию настроек в config через `/save`.

6. **Скопирование команд.**
   Команды настройки (`/model`, `/temperature`, ...) применяются **к активному агенту**.
   `config.json` — глобальный дефолт для новых агентов; `/save` записывает состояние
   активного агента в файл сессии.

7. **Два уровня состояния.**
   - Runtime-состояние агента (меняется командами на лету).
   - `config.json` — источник истины по умолчанию; `/save` записывает runtime-состояние
     активного агента, `/load` читает его в активного агента.

8. **Системный промпт.**
   - По умолчанию: `./SYSTEM_PROMPT.md` в корне; каждый новый агент получает его.
   - Переопределение при запуске: `--system-prompt <path>` — для стартового агента.
   - В сессии: `/system` — посмотреть текущий; `/system <path>` — заменить у активного агента.
   - Если файл не найден — ошибка с понятным сообщением, запуск не стартует.

9. **Модель = `provider:model`, резолв на запрос.**
   Каждый `Agent` хранит id модели в формате `provider:model`. При `ask()` агент резолвит
   провайдера из config и передаёт `api_base`/`api_key` в запрос. `LLMClient` общий для
   всех агентов, но параметры коннекта берутся от провайдера выбранной модели —
   агент может в одном чате переключаться между провайдерами.

## 5. Формат config.json (multi-provider)

```json
{
  "providers": {
    "openai": {
      "api_base": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    },
    "ollama": {
      "api_base": "http://localhost:11434/v1",
      "api_key": "",
      "models": ["llama3.1", "qwen2.5-coder"]
    }
  },
  "default_model": "openai:gpt-4o-mini",
  "temperature": 0.7,
  "top_p": 1,
  "max_tokens": 4096,
  "stop": []
}
```

- `providers` — именованные провайдеры: `api_base` (любой OpenAI-compatible эндпоинт,
  клиент сам дописывает `/chat/completions`), `api_key` (может быть пустым, например для ollama),
  `models` — список моделей.
- Модель идентифицируется как `provider:model` (например `ollama:llama3.1`).
- `default_model` — дефолтная модель для новых агентов; pydantic валидирует,
  что она существует в одном из провайдеров, и что `provider` части соответствует
  известному провайдеру.
- `temperature`/`top_p`/`max_tokens`/`stop` — глобальные дефолты; переопределяются
  на агента командами.
- Список моделей — только из config (авто-обнаружения через `/models` нет).
- `config.json` добавить в `.gitignore`.
- Валидация через pydantic: при невалидном config — вывод ошибок по полям.

## 6. Команды v1

| Команда | Описание |
|---|---|
| `/help` | список всех команд |
| `/new [name]` | создать нового агента (вкладку) с настройками из config.json |
| `/close` | закрыть активного агента (подтверждение, если есть незавершённый запрос) |
| `/name <name>` | переименовать активного агента (видно на вкладке) |
| `/model [provider:model]` | без аргумента — палитра выбора модели (фильтр, ↑↓ — навигация, Enter — применить, Esc — отмена; текущая помечена `*`); с аргументом — выбрать модель активного агента. Tab-completion по списку из config; неизвестная модель — ошибка со списком доступных |
| `/temperature <0..2>` | сменить температуру |
| `/top-p <0..1>` | сменить top_p |
| `/max-tokens <n>` | сменить max_tokens |
| `/stop <seq1,seq2>` | установить stop-sequences (`, ` — разделитель; `""` — очистить) |
| `/system [path]` | показать текущий системный промпт / заменить на файл `path` |
| `/history` | показать текущую историю диалога |
| `/clear` | очистить историю сессии |
| `/save [file]` | сохранить сессию (история + настройки агента) в jsonl (по умолчанию `sessions/<timestamp>.jsonl`) |
| `/load <file>` | загрузить сессию из файла в активного агента |
| `/exit` (или Ctrl+Q) | выход |

Поведение:
- `/<неизвестная команда>` — подсказка + список команд.
- Tab-completion по первым символам `/<cmd>` и аргументам.
- Команды, меняющие настройки, помечаются «не сохранено», если отличается от config.json;
  `/save` синхронизирует.
- Переключение вкладок: binding `Ctrl+Tab` / `Ctrl+Shift+Tab` (Textual), имя и модель
  видны на вкладке; индикатор, если агент активно стримит.

## 7. Поведение чата

1. Пользователь вводит текст → не начинается с `/` → передать в `agent.ask()` активного агента.
2. `agent.ask()`:
   - добавить user-сообщение в `SessionMemory`;
   - резолв провайдера из id модели (`provider:model`) — `api_base`/`api_key` запроса;
   - `ContextBuilder.build_messages()`;
   - `await llm.astream(request)` — токены доставляются в состояние агента
     (Textual worker / message pump); виджет `MessageList` батчит перерисовки
     (~100 мс), не рендеря на каждый токен;
   - по окончании — добавить assistant-сообщение в `SessionMemory`;
   - при tool_calls в ответе (задел): выполнить через `ToolRegistry`, вернуть `tool`-ответы
     в следующий запрос того же хода.
3. **Параллельные стримы:** стриминг в нескольких агентах идёт одновременно
   (asyncio, I/O-bound); блокировки на агента (один активный запрос на агента —
   новый ввод дождётся завершения или будет отменён первым Ctrl+C).
4. Ошибки сети/4xx/5xx: ретраи 3 раза (exponential backoff 1s/2s/4s) только на
   429/5xx/сеть; 401/400 — показать сообщение об ошибке в этом агенте и продолжить чат.
5. Ctrl+C: первый раз — отменить текущий запрос активного агента; повторный — выход.
   Ctrl+Q — выход.

## 8. Заделы (интерфейсы в v1, реализация позже)

- `LongTermMemory` (my_agent/memory/longterm.py): протокол `recall(query) -> list[Chunk]`,
  `store(messages)`. Stub возвращает `[]`. Вопрос per-agent vs общая память — решать при реализации.
- `ToolRegistry` (my_agent/tools/registry.py): `register(tool)`, `get(names)`, `all()`.
- `McpAdapter` (my_agent/tools/mcp.py): подключение MCP-сервера (stdio/streamable-http),
  маппинг его tools в `Tool` (использовать официальный `mcp` Python SDK). Stub.
- RAG-интерфейс: `KnowledgeBase.search(query) -> list[Chunk]` — подключается в `ContextBuilder`.
  Stub.

Все заделы — только протоколы/интерфейсы + stub'ы с docstring, чтобы контракт был зафиксирован.

## 9. Порядок реализации (v1)

1. Каркас: pyproject.toml (uv), структура пакетов, ruff/mypy/pytest конфиги.
2. `config/`: pydantic-схема, load/save, значения по умолчанию.
3. `llm/client.py`: astream + ретраи; ручной smoke-тест против локального
   openai-совместимого сервера.
4. `core/`: модели message, ContextBuilder, класс `Agent.ask()` (сначала без UI, console).
5. `ui/`: Textual App, вкладка-агент, ChatInput, MessageList со стримингом, StatusBar.
6. `commands/`: реестр + все команды v1 + tab-completion.
7. Мульти-агентность: `/new`, `/close`, `/name`, TabbedContent, фоновые стримы.
8. `memory/session.py`: /save, /load (jsonl).
9. Заделы: интерфейсы + stub'ы (longterm, tools, mcp, rag).
10. README с инструкцией по запуску, `.gitignore` (config.json, sessions/, `.venv/`, `__pycache__/`).

## 10. Критерии готовности v1

- `uv run my-agent` запускает чат; стриминг ответов работает.
- Все команды v1 из таблицы работают.
- `/new` создаёт второго агента; можно одновременно стримить в двух вкладках;
  переключение не теряет вывод.
- `--system-prompt <path>` переопределяет системный промпт стартового агента.
- `/save` + `/load` восстанавливают сессию после перезапуска.
- Смена `/model` между провайдерами (например, openai ↔ ollama) работает в одном чате;
  StatusBar показывает `provider:model`.
- config.json валидируется pydantic, ошибки понятные.
- `ruff check`, `mypy my_agent`, `pytest` проходят.
