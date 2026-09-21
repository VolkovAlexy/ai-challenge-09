# AGENTS.md

## What this repo is

Single `uv` project at the root: `agent` — a chat agent for OpenAI-compatible LLM APIs with
a Vue SPA (`web/`) served by a FastAPI backend (`agent/web_server/`).

Git HEAD still contains an older layout (`agent/`, `day-01/`, `day-02/`, `day-04/`) that has been
deleted in the working tree — `git status` shows ~100 deletions plus untracked root files. That is
the intended direction; do not resurrect those directories.

Specs of record (read before architectural changes, all Russian):
- `my_agent.MD` — core spec (config format, commands, architecture decisions).
- `WEB_UI.MD` — frontend spec; **§5 is the normative HTTP/SSE contract**.
- `BACKEND.md` — how the core maps onto the HTTP endpoints + required test cases.

## Commands (always from the repo root)

```bash
uv sync
uv run agent-dev                # backend (127.0.0.1:8321) + vite dev server (:5173) together
uv run agent-web                # backend only on 127.0.0.1:8321 (--port/MY_AGENT_PORT, --config, --sessions, --reload)
./run-web.sh                    # shell-скрипт: backend + vite dev server

uv run ruff check .             # QA order: ruff → mypy → pytest
uv run mypy agent               # strict; package only, not tests/
uv run pytest                   # single test: uv run pytest tests/test_agent.py -k <name>

cd web && npm install
npm run dev                     # vite :5173, proxies /api → AGENT_API (default http://127.0.0.1:8321)
npm run typecheck               # vue-tsc --noEmit
npm test                        # vitest run; single file: npx vitest run src/api/sse.test.ts
```

Running from another directory breaks things: `config.json`, `SYSTEM_PROMPT.md` and `sessions/`
are all resolved relative to CWD (`agent/config/store.py:10`,
`agent/web_server/app.py:32`).

If `uv run <tool>` fails with "Failed to spawn … No such file or directory" after the project
directory was moved or renamed, fix with `uv sync --reinstall` (venv entry points hardcode paths).

## Conventions

- Ruff line-length 100; `RUF001-003` ignored — Cyrillic in strings/docstrings is intentional.
  All user-facing strings and docstrings are Russian; match that.
- mypy is `strict` + `warn_unreachable`; untyped imports allowed only for
  `httpx_sse.*`. Type hints are mandatory.
- pytest `asyncio_mode = "auto"` — async tests need no marker. LLM is always mocked
  (`MockLLM` pattern in `tests/test_agent.py`); no test hits a real provider.
- TS is strict with `noUnusedLocals`/`noUnusedParameters`; `@/…` aliases `web/src/`.

## Architecture rules

- State lives in plain-Python classes; Vue is a thin render layer. One `Agent`
  per chat tab; `LLMClient`, `ToolRegistry`, `Config`, `SessionStore` are shared per process.
- `ContextBuilder.build_messages()` (`agent/core/context.py`) is the only place the messages
  array is assembled. Context strategies are `none | summary | sliding | facts`
  (compaction, sliding window, sticky facts); branches live in `agent/memory/branching.py`.
- LLM access is a hand-rolled `httpx` + `httpx-sse` client: POST `{api_base}/chat/completions`,
  SSE, retries 3× (1s/2s/4s) on transient/5xx/429 only. No openai SDK.
- Model ids are `provider:model`, validated against `config.json` providers.
- `LongTermMemory`, `ToolRegistry`, `McpAdapter`, `KnowledgeBase` are protocol stubs by design —
  don't implement them without a spec change.
- Sessions autosave to SQLite (`sessions/sessions.db`) on every change; there is no `/save`.
  `/export` writes jsonl to `sessions/<ts>.jsonl`.

## Web backend gotchas

- `agent/web_server/stream.py` does **not** read LLM chunks. It polls public `Agent` state
  (`streaming_text`, `is_compacting`, `compaction_note`) every ~100 ms and emits deltas. An SSE
  client disconnecting does not cancel the agent's turn.
- The SSE event name is carried **inside** the `data:` JSON as an `event` field; the frontend
  parser (`web/src/api/sse.ts`) ignores the real SSE `event:` line. Don't "fix" this.
- `compaction_done` fields are recovered by regex-parsing the Russian `compaction_note` string
  (`_COMPACTION_RE` in `stream.py`). Changing that note's wording silently breaks the web event.
- Errors are always `{"detail": string}`; 409 means "agent is already streaming".
- `api_key` must never appear in any response; there is a test asserting the string is absent
  from `GET /api/config`.

## Secrets / local files

`config.json` (real API keys) and `sessions/` are gitignored — never commit them and never print
their contents. There is no CI; verification is the five QA commands above.