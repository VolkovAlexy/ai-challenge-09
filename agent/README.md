# my_agent

CLI-агент: TUI-чат с LLM по OpenAI-compatible API (стриминг SSE),
мульти-агентность (вкладки) и slash-команды для настройки.

## Запуск

```bash
uv sync          # зависимости
uv run my-agent  # запуск чата
```

Опции:

```bash
uv run my-agent --config config.json          # свой config
uv run my-agent --system-prompt my.md         # промпт стартового агента
uv run my-agent --model openai:gpt-4o-mini    # модель стартового агента
```

## Настройка

`config.json` в корне (в `.gitignore` — содержит API-ключи):

```json
{
  "providers": {
    "openai":  {"api_base": "https://api.openai.com/v1", "api_key": "sk-…", "models": {"gpt-4o-mini": 128000}},
    "ollama":  {"api_base": "http://localhost:11434/v1", "api_key": "", "models": {"llama3.1": 131072, "qwen2.5-coder": null}}
  },
  "default_model": "ollama:llama3.1",
  "temperature": 0.7,
  "top_p": 1.0,
  "max_tokens": 4096,
  "stop": [],
  "context_window_default": 32768,
  "compaction_threshold": 0.85
}
```

Модель — `provider:model`; список моделей только из config. `api_key` может
быть пустым (ollama). `models` — модель → размер контекстного окна в токенах
(`null` — окно неизвестно, возьмётся `context_window_default`). Когда контекст
заполняет `compaction_threshold` окна, начало беседы сжимается в саммари для
LLM — чат при этом не меняется, вся история остаётся на экране и в сессии
(заметка в чате, в статус-баре — `context N/окно (P%)`). В статус-баре —
накопительный расход сессии: `in X out Y Σ Z` (in — промпты запросов, out —
ответы, Σ — всего; `~` — есть оценки; обнуляются `/clear` и `/session`).
Под репликами ассистента — `tokens: in … · out …`. Файла нет —
создаётся дефолтный; невалидный — понятные ошибки по полям.

## Команды

| Команда | Описание |
|---|---|
| `/help` (F1) | список команд |
| `/new [name]` | новый агент-вкладка |
| `/close` | закрыть агента (повтор — подтверждение при активном запросе) |
| `/name <name>` | переименовать агента |
| `/model [provider:model]` | без аргумента — палитра выбора модели (фильтр, ↑↓, Enter); с аргументом — смена по имени |
| `/temperature <0..2>` | температура |
| `/top-p <0..1>` | top_p |
| `/max-tokens <n>` | max_tokens |
| `/stop <a,b>` | stop-sequences (`""` — очистить) |
| `/system [path]` | показать / заменить системный промпт |
| `/history` | история диалога |
| `/clear` | очистить историю |
| `/session` | палитра всех сессий; выбор загружает сессию в активного агента (фильтр, ↑↓, Enter) |
| `/export [file]` | экспорт сессии в jsonl (по умолчанию `sessions/<ts>.jsonl`) |
| `/exit` | выход |

Tab — completion команд и моделей (readline-стиль: повторный Tab перебирает
кандидатов). Ctrl+Tab / Ctrl+Shift+Tab — вкладки. Ctrl+C — 1-й раз отмена
запроса, повторный — выход. Ctrl+Q — выход.

## Архитектура (кратко)

- `Agent` — plain-Python класс, инстанс на чат: свои настройки (клон дефолтов
  config + runtime-override), промпт, `InMemorySession`. Общие ресурсы
  (`LLMClient`, `ToolRegistry`, `Config`) — шаред.
- Textual — тонкий рендер: состояние в агентах, стриминг в неактивной вкладке
  продолжается, при переключении вывод догоняется; перерисовка батчится ~100 мс.
- Сессии автосохраняются в SQLite (`sessions/sessions.db`) при каждом изменении —
  ручного `/save` нет; `/session` выбирает и продолжает любую сохранённую сессию,
  `/export` выгружает в jsonl.
- `LLMClient` — httpx + httpx-sse: POST `/chat/completions`, SSE, ретраи 3×
  (1s/2s/4s) на 429/5xx/сеть; 400/401 — ошибка в чате, чат продолжается.
- `ContextBuilder.build_messages(...)` — единственная точка сборки messages
  (задел под RAG/память).
- Заделы (протоколы + stub'ы): `LongTermMemory`, `ToolRegistry`, `McpAdapter`,
  `KnowledgeBase`.

## QA

```bash
uv run ruff check .
uv run mypy my_agent
uv run pytest
```
