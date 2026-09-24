# agent

Веб-агент: чат с LLM по OpenAI-compatible API (стриминг SSE),
мульти-агентность (вкладки) и slash-команды для настройки.

## Запуск

```bash
uv sync           # зависимости
uv run agent-dev  # запуск backend + vite dev server (http://localhost:5173)
```

Или по отдельности:

```bash
uv run agent-web                              # backend на 127.0.0.1:8321
cd web && npm install && npm run dev          # vite dev server на :5173
```

Опции backend:

```bash
uv run agent-web --config config.json     # свой config
uv run agent-web --port 8321              # порт (или MY_AGENT_PORT)
uv run agent-web --sessions sessions/     # директория сессий
uv run agent-web --reload                 # перезагрузка при изменении кода
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
  "compaction_threshold": 0.6
}
```

Модель — `provider:model`; список моделей только из config. `api_key` может
быть пустым (ollama). `models` — модель → размер контекстного окна в токенах
(`null` — окно неизвестно, возьмётся `context_window_default`). Когда контекст
заполняет `compaction_threshold` окна, начало беседы сжимается в саммари для
LLM — чат при этом не меняется, вся история остаётся на экране и в сессии
(заметка в чате, в статус-баре — `context N/окно (P%)`). В статус-баре —
накопительный расход сессии: `in X out Y Σ Z` (in — промпты запросов, out —
ответы, Σ — всего; `~` — есть оценки).
Под репликами ассистента — `tokens: in … · out …`. Файла нет —
создаётся дефолтный; невалидный — понятные ошибки по полям.

## Команды

Остались только две слэш-команды; всё остальное вынесено в UI (панели и «Настройки»).

| Команда | Описание |
|---|---|
| `/close` | закрыть агента (повтор — подтверждение при активном запросе) |
| `/export [file]` | экспорт сессии в jsonl (по умолчанию `sessions/<ts>.jsonl`) |

### Настройки агента (панель «Настройки»)

Параметры генерации и контекст активного агента редактируются в правой панели
(вкладка «Настройки») и сохраняются **в сессию агента** (`PATCH /api/agents/{id}`;
глобальный `config.json` не изменяется):

- генерация: `temperature` (0..2), `top_p` (0..1), `max_tokens`, `stop`;
- системный промпт: просмотр и путь к файлу;
- контекст: стратегия `context_strategy` (`none | summary | sliding | facts`)
  выбирается группой кнопок; ниже появляется настройка выбранной стратегии:
  `sliding`/`facts` — `sliding_window`, `summary` — `compaction_threshold`
  (слайдер в % от 50 до 100), `none` — без параметров;
- факты: блок «Факты» для стратегии `facts`.

## Архитектура (кратко)

- `Agent` — plain-Python класс, инстанс на чат: свои настройки (клон дефолтов
  config + runtime-override), промпт, `InMemorySession`. Общие ресурсы
  (`LLMClient`, `ToolRegistry`, `Config`) — шаред.
- Vue SPA — тонкий рендер: состояние в агентах, стриминг продолжается в фоне,
  при переключении вкладки вывод догоняется.
- Сессии автосохраняются в SQLite (`sessions/sessions.db`) при каждом изменении —
  ручного `/save` нет; выбор сессии и «Новый чат» — в левой панели,
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
uv run mypy agent
uv run pytest
```