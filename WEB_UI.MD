# my_agent — Web UI (Vue) — спецификация

Веб-версия чата my_agent: та же функциональность и поведение, что у TUI-версии
(ядро описано в `my_agent.MD` — этот документ его не дублирует, а переиспользует),
но интерфейс — браузерное SPA на Vue. Логика бэкенда (агенты, LLM-клиент, сессии,
компакция) остаётся в Python-пакете `my_agent`; для веб-UI она оборачивается в
HTTP API. Этот документ фиксирует: стек и структуру фронтенда, архитектуру
состояния, контракт с бэкендом, поведение всех экранов и элементов, тесты.

## 1. Цели и рамки

- Полный паритет с TUI v1: мульти-агентность (вкладки), slash-команды, стриминг,
  палитры (модель / сессии / помощь), статус-бар, автосохранение, компакция
  контекста, счётчики токенов.
- Состояние — вне компонентов (как в TUI: «состояние вне UI»): Vue-компоненты —
  тонкий слой рендеринга.
- Стриминг в неактивной вкладке продолжается; при переключении вкладка «догоняет»
  вывод из стора (автоматически — источник истины один).
- API-ключи никогда не покидают бэкенд: фронтенд не получает и не отправляет
  `api_key`; все запросы к LLM — только через бэкенд.
- Вне рамок документа: реализация HTTP-бэкенда (рекомендация: FastAPI + SSE),
  деплой, авторизация (приложение локальное).

## 2. Стек

| Слой | Выбор | Обоснование |
|---|---|---|
| Фреймворк | Vue 3 + TypeScript (`<script setup>`) | Composition API, строгая типизация |
| Сборка | Vite | дев-сервер с прокси на бэкенд, быстрый HMR |
| Состояние | Pinia | де-факто стандарт для Vue 3; сторы — аналог «plain-Python классов» из TUI |
| Тесты | Vitest + @vue/test-utils + happy-dom | юнит-тесты сторов/утилит, компонентные тесты, fake timers |
| Типизация | `vue-tsc --noEmit` в QA-контуре | strict TS обязателен |
| Markdown | `markdown-it` + `dompurify` | рендер ответов ассистента; пользовательский ввод — только escape |
| Транспорт | `fetch` + собственный SSE-парсер (~50 строк) | без openai-SDK и тяжёлых клиентов; парсер тестируется изолированно |
| Роутер | нет | одно окно чата; вкладки — состояние стора, не URL |

UI-кит не используется: стили свои, на CSS-переменных (тёмная тема, близкая
к TUI). Все пользовательские строки — на русском (как в TUI).

## 3. Структура проекта

```
web/
├── package.json               # имя my-agent-web; scripts: dev/build/test/typecheck
├── vite.config.ts             # dev-прокси /api → AGENT_API (по умолч. http://127.0.0.1:8321)
├── vitest.config.ts           # environment: happy-dom; setup-файл
├── tsconfig.json
├── index.html
└── src/
    ├── main.ts
    ├── App.vue                # каркас: TabBar + активный ChatView + палитры (teleport)
    ├── api/
    │   ├── types.ts           # DTO: ConfigDTO, AgentDTO, MessageDTO, UsageDTO, события стрима
    │   ├── client.ts          # fetch-обёртка: все REST-эндпоинты, маппинг ошибок
    │   └── sse.ts             # SSE-парсер поверх fetch(POST) + ReadableStream
    ├── stores/
    │   ├── config.ts          # конфиг с бэкенда: провайдеры, модели, дефолты
    │   ├── agents.ts          # N агентов, activeAgentId, стримы, счётчики
    │   └── sessions.ts        # список сессий, загрузка, экспорт
    ├── commands/
    │   └── registry.ts        # клиентский реестр slash-команд: {name, description, args, handler, complete?}
    ├── composables/
    │   └── useChatStream.ts   # запуск/отмена стрима одного агента, батчинг дельт
    ├── components/
    │   ├── TabBar.vue         # вкладки: имя, модель, индикатор стрима; кнопка «+»
    │   ├── ChatView.vue       # композиция списка, ввода, статус-бара
    │   ├── chat/MessageList.vue
    │   ├── chat/MessageItem.vue
    │   ├── chat/ChatInput.vue # textarea + slash-меню + tab-completion
    │   ├── chat/StatusBar.vue
    │   ├── chat/HistoryModal.vue        # просмотр /history
    │   ├── palettes/OverlayPalette.vue  # общий каркас: фильтр + список + клавиатура
    │   ├── palettes/ModelPalette.vue
    │   ├── palettes/SessionPalette.vue
    │   ├── palettes/HelpPalette.vue
    │   └── common/ConfirmDialog.vue
    └── styles/
        └── main.css           # CSS-переменные темы, layout
```

## 4. Архитектура состояния

1. **Один стор — много агентов.** `useAgentsStore()` хранит
   `agents: Map<AgentId, AgentState>` и `activeAgentId`. `AgentState` — аналог
   Python-класса `Agent`: имя, `model` (`provider:model`), настройки
   (temperature / top_p / max_tokens / stop / системный промпт), история
   (`MessageDTO[]`), флаги (`streaming`, `compacting`), счётчики
   (`tokensIn`, `tokensOut`), `contextUsed` / `contextWindow`. Компоненты только
   читают стор и вызывают действия.
2. **Единственный источник истины — стор.** Стрим пишется в `AgentState`
   инкрементально; неактивная вкладка не рендерится, но стор обновляется.
   Переключение вкладки — смена `activeAgentId`; «догонять» ничего не надо
   (reactivity). История не дублируется в локальном state компонентов.
3. **Настройки применяются к активному агенту** (как в TUI, §6): команды
   `/model`, `/temperature` и т.д. → `PATCH` агента на бэкенде → стор
   обновляется из ответа. `config.json` — только дефолты для `/new`
   (фронтенду отдаётся без ключей).
4. **Один активный запрос на агента.** Ввод во время стрима этого же агента
   блокируется (кнопка «Стоп» активна). У разных агентов стримы идут параллельно.
5. **Отмена.** Кнопка «Стоп» и `Esc` (когда фокус в поле ввода и идёт стрим) →
   `POST /api/agents/{id}/cancel`; локально стрим помечается отменённым,
   частичный ответ остаётся (аналог Ctrl+C в TUI).
6. **Автосохранение — на бэкенде** (SQLite). Фронтенд ничего не персистит,
   кроме `activeAgentId` в `localStorage` (удобство, не источник истины).
   После перезагрузки страницы — `GET /api/agents` + истории.

## 5. Контракт с бэкендом (нормативный)

Базовый префикс `/api`. Все тела — JSON. Ошибки — `{ "detail": string }` с
осмысленным HTTP-статусом (400/401/404/409). Ключи API никогда не возвращаются.

### 5.1 REST

| Метод и путь | Назначение (команда TUI) |
|---|---|
| `GET /api/config` | конфиг без ключей: провайдеры с моделями и окнами, дефолты, `compaction_threshold` |
| `GET /api/commands` | реестр команд `[{name, description, args_spec}]` — источник `/help` и автодополнения |
| `GET /api/system-prompt` | текущий системный промпт (`/system` без аргумента) |
| `PUT /api/system-prompt` `{path}` | заменить файл системного промпта (`/system <path>`) |
| `GET /api/agents` | список агентов (восстановление после перезагрузки страницы) |
| `POST /api/agents` `{name?}` | новый агент из дефолтов config (`/new [name]`) → `AgentDTO` |
| `DELETE /api/agents/{id}` | закрыть агента (`/close`; 409, если идёт стрим) |
| `PATCH /api/agents/{id}` | поля: `name`, `model`, `temperature`, `top_p`, `max_tokens`, `stop`, `system_prompt_path` (`/name`, `/model`, `/temperature`, `/top-p`, `/max-tokens`, `/stop`, `/system`) |
| `GET /api/agents/{id}/messages` | история агента (`/history`, восстановление) |
| `DELETE /api/agents/{id}/messages` | очистить историю (`/clear`) |
| `GET /api/sessions` | список сохранённых сессий (`/session`) |
| `POST /api/agents/{id}/load-session` `{session_id}` | загрузить сессию в агента (`/session` → Enter) |
| `POST /api/agents/{id}/export` `{path?}` | экспорт в jsonl → `{path}` (`/export [file]`) |
| `POST /api/agents/{id}/messages` `{content}` | отправить сообщение; ответ — SSE-поток (§5.2) |
| `POST /api/agents/{id}/cancel` | отменить текущий ход агента |

`AgentDTO`: `{ id, name, model, settings: {temperature, top_p, max_tokens, stop},
system_prompt: {path, content}, context_used, context_window, streaming,
compacting, scratchpad, memory_suggestion }`. `MessageDTO`: `{ id,
role: "user"|"assistant"|"system"|"tool", content,
tool_name?: string, tool_calls?: string[], usage?: UsageDTO,
error?: {kind, detail} }` (`tool_name` — имя инструмента у роли `tool`;
`tool_calls` — имена инструментов, вызванных ассистентом). `UsageDTO`:
`{ prompt_tokens?, completion_tokens?, reasoning_tokens?, approx?: boolean }`
(`approx` — оценка chars/4, показывается с `~`).

### 5.2 SSE-поток ответа

`POST /api/agents/{id}/messages` отвечает `text/event-stream`; каждое событие —
`data: <json>\n\n`. Типы событий:

| Событие | Поля | Действие фронтенда |
|---|---|---|
| `user_message` | `message: MessageDTO` | добавить в историю |
| `compaction_started` | — | лоадер «сжимаю контекст…» |
| `compaction_done` | `removed, summary_tokens, pct_before, pct_after` | заметка `⇄ Контекст сжат: -X удалено, +Y саммари (A% → B%)` |
| `delta` | `content` | дописать в текущее assistant-сообщение (батчинг, §7) |
| `tool_message` | `message: MessageDTO` | артефакт tool-раунда: assistant с `tool_calls` либо результат инструмента (роль `tool`); вставить в историю (replace стрим-заглушки для assistant) |
| `scratchpad` | `content` | обновить рабочую память (панель scratchpad) в live-режиме |
| `done` | `message: MessageDTO` (с `usage`) | зафиксировать сообщение, начислить счётчики |
| `cancelled` | — | пометить ход отменённым |
| `error` | `kind: "http"|"network"`, `detail` | показать ошибку как system-сообщение в чате; чат продолжается |
| `memory_suggestion` | `content` | баннер «Предлагаю запомнить…» (после `done`) |

Порядок при tool-раундах: `user_message → (compaction_*) → delta* →
(tool_message | scratchpad)* → delta* → done → (memory_suggestion)`.

Ретраи 429/5xx/сеть (3×, 1s/2s/4s) — ответственность бэкенда; фронтенд
отображает только исход. Разрыв соединения → `error {kind:"network"}`.

### 5.3 SSE-парсер (клиент)

Модуль `api/sse.ts`: `async function* sseEvents(response)` — инкрементальный
парсер над `response.body` (UTF-8, `TextDecoder({stream:true})`): разбиение по
`\n\n`, устойчивость к разрыву события между чанками, игнор полей `event:`/`id:`.
`[DONE]` не ожидается: терминалы — события `done` / `cancelled` / `error` или
закрытие потока.

## 6. Команды (slash) — поведение в UI

Реестр команд фронтенд получает из `GET /api/commands` и дополняет клиентскими
обработчиками. Таблица соответствия:

| Команда | UI-поведение |
|---|---|
| `/help` | палитра HelpPalette: список команд из реестра (фильтр, ↑↓, Esc) |
| `/new [name]` | `POST /api/agents`, новая вкладка, фокус в ввод |
| `/close` | если агент стримит — ConfirmDialog («Есть незавершённый запрос»), затем `DELETE /api/agents/{id}`; закрывается активная вкладка |
| `/name <name>` | `PATCH {name}` — видно на вкладке |
| `/model [provider:model]` | без аргумента — ModelPalette (фильтр, ↑↓, Enter, Esc, текущая помечена `*`); с аргументом — `PATCH {model}`; неизвестная модель — ошибка со списком доступных (из `GET /api/config`) |
| `/temperature <0..2>`, `/top-p <0..1>`, `/max-tokens <n>`, `/stop <seq,...>` | лёгкая валидация аргументов на клиенте → `PATCH` |
| `/system [path]` | без аргумента — модалка с текущим промптом; с путём — `PUT /api/system-prompt` |
| `/history` | HistoryModal: полная история активного агента (plain text) |
| `/clear` | ConfirmDialog → `DELETE .../messages`; счётчики in/out/Σ обнуляются |
| `/session` | SessionPalette: список сессий (фильтр, ↑↓, Enter, Esc, текущая `*`); выбор → `POST load-session` |
| `/export [file]` | `POST export` → system-сообщение с путём файла |
| `/exit` | закрывает вкладку агента; закрытие последней вкладки — пустое состояние с кнопкой «Новый чат» (окно браузера не закрываем) |

Неизвестная команда → inline-подсказка в чате + список команд (как в TUI).
Tab в `ChatInput`: по префиксу `/<cmd>` — дополнение имени команды; для
`/model` — дополнение по списку моделей; повторный Tab — цикл по вариантам.

## 7. Поведение чата и рендеринг

1. **Отправка.** Пустой ввод игнорируется; ввод, начинающийся с `/`, парсится
   как команда, иначе — `POST messages`.
2. **Стриминг с батчингом.** Дельты копятся в буфер; стор обновляется
   раз в ~100 мс (таймер), не на каждый токен. После `done` — финальный flush.
   Неактивная вкладка: стор обновляется так же, рендера нет.
3. **Скролл.** Автоскролл вниз только если пользователь у нижнего края
   (порог ~40 px); при прокрутке вверх автоскролл отключается до возврата вниз.
   Кнопка «Вниз ↓» появляется при отставании.
4. **Markdown.** Ответы ассистента — markdown (fenced code, списки, таблицы)
   через `markdown-it` + `dompurify`; во время стрима перерендер батчится вместе
   с дельтами. Сообщения пользователя — plain text с escape. Код-блоки —
   моношрифт, кнопка «Копировать».
5. **Строки токенов.** Под репликой ассистента:
   `tokens: in … · out … · think …` (think — только если были размышления;
   при `approx` — префикс `~`).
6. **Статус-бар** (низ активного чата):
   `provider:model · T 0.7 · top-p 1 · max 4096 · context 12345/32768 (38%) ·
   in 5000 out 1200 Σ 6200` + индикатор состояния (стрим / «сжимаю контекст…» /
   стоп). При заполнении окна выше `compaction_threshold` — часть
   `context …` подсвечивается жёлтым (как в TUI).
7. **Ошибки.** `error`-сообщение рендерится в ленте отдельным стилем (красная
   рамка); 400/401 не ретраятся и не ломают чат.
8. **Вкладки.** TabBar: имя + модель + точка-индикатор стрима. Переключение:
   клик, `Ctrl+Tab` / `Ctrl+Shift+Tab` (браузер может перехватывать — дублируем
   `Alt+←/→` и `Alt+1..9`). Закрытие вкладки — крестик (ConfirmDialog при
   активном стриме).
9. **Пустое состояние.** Нет агентов — экран «Новый чат» с одной кнопкой.

## 8. Палитры (общий каркас)

`OverlayPalette.vue`: модальный оверлей, поле фильтра, список опций, клавиатура
(`↑↓` — навигация, `Enter` — выбрать, `Esc` — отмена; текущий элемент помечен
`*`). Наследники передают источник опций и обработчик выбора. Открытие палитры
гасит фокус ввода, закрытие — возвращает.

## 9. Тестирование (Vitest)

Юнит-тесты:
- `api/sse.ts`: несколько событий в одном чанке, разрыв события между чанками,
  CRLF, обрыв соединения.
- `stores/agents.ts`: создание/закрытие/переключение агентов; начисление usage;
  флаги `streaming`/`compacting`; параллельные стримы двух агентов не путают
  историю; переключение вкладок без потерь.
- `commands/registry.ts`: парсинг `/cmd args`, автодополнение по префиксу,
  неизвестная команда, валидация аргументов (`/temperature` и др.).
- `composables/useChatStream.ts`: батчинг дельт (fake timers: N дельт → 1
  обновление за 100 мс; финальный flush после `done`), отмена.

Компонентные тесты (@vue/test-utils, happy-dom):
- `ChatInput`: slash-меню, навигация ↑↓ / Enter / Esc, Tab-дополнение.
- `StatusBar`: формат строки, жёлтый индикатор при превышении порога.
- `MessageItem`: markdown-рендер, footer токенов, стиль ошибки.
- `ModelPalette` / `SessionPalette` / `HelpPalette`: фильтр, выбор, Esc, метка `*`.
- `TabBar`: активная вкладка, индикатор стрима, закрытие.

`api/client.ts` тестируется с замоканным `fetch` (маппинг ошибок, 409 при
`/close` во время стрима). Скрипты: `npm test` (vitest run), `npm run coverage`.

## 10. Порядок реализации

1. Каркас: `web/` — Vite + Vue + TS, Pinia, Vitest + happy-dom,
   `vite.config.ts` с прокси `/api`, QA-скрипты (`test`, `typecheck`).
2. `api/`: типы DTO, `client.ts`, `sse.ts` (+ тесты парсера на мок-стримах).
3. `stores/`: config, agents, sessions (+ тесты на мок-клиенте).
4. `ChatView` без стриминга: отправка сообщения, отображение истории,
   статус-бар.
5. Стриминг: `useChatStream` (батчинг, отмена, ошибки) + `MessageList` /
   `MessageItem`.
6. `commands/registry.ts` + `ChatInput` (slash-меню, Tab-дополнение).
7. Палитры: Help, Model, Session + ConfirmDialog, HistoryModal.
8. Мульти-агентность: TabBar, `/new` / `/close` / `/name`, параллельные стримы,
   восстановление после перезагрузки страницы.
9. Полировка: скролл, пустое состояние, тема, строки токенов, заметка о
   компакции.

## 11. Критерии готовности

- `npm run dev` поднимает UI, подключается к бэкенду, стриминг ответов работает.
- Все команды v1 из §6 работают в UI; паритет с TUI по поведению (spec TUI §6–7).
- `/new` создаёт второго агента; два агента стримят одновременно; переключение
  вкладок не теряет вывод; после перезагрузки страницы агенты и истории
  восстанавливаются.
- `/model` между провайдерами в одном чате; статус-бар показывает
  `provider:model`, счётчики `in/out/Σ`, жёлтый индикатор контекста.
- Заметка о сжатии контекста и лоадер «сжимаю контекст…» отображаются.
- Отмена хода (Стоп / Esc) работает; ошибка 400/401 показывается в чате,
  чат продолжается.
- `api_key` нигде не появляется на фронтенде (проверить тестом на `GET /api/config`).
- `npm test`, `npm run typecheck` — зелёные.
