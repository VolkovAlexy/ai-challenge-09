# AGENTS.md

## Layout

- Collection of independent `uv` projects (no shared workspace). Run all commands from inside the project directory — nothing works from the repo root.
  - `agent/` — main project: `my_agent`, a Textual TUI chat for OpenAI-compatible LLM APIs.
  - `day-01/`, `day-02/`, `day-04/` — standalone course exercises (`one-question` CLI). Not dependencies of `agent/`.
- Spec of record for `agent/`: `my_agent.MD` (full spec) and `PROMPT.md` (acceptance criteria). Read before architectural changes.

## agent/ — commands

```bash
uv sync                    # deps
uv run my-agent            # run the TUI
uv run ruff check .        # QA order: ruff → mypy → pytest
uv run mypy my_agent       # strict; targets the package only, not tests/
uv run pytest              # single test: uv run pytest tests/test_agent.py -k <name>
```

- pytest `asyncio_mode = "auto"` — async tests need no marker.
- venv entry-point scripts hardcode absolute paths. If the project directory was moved/renamed and `uv run <tool>` fails with "Failed to spawn … No such file or directory", fix with `uv sync --reinstall`.
- mypy is strict with `warn_unreachable`; untyped imports allowed only for `textual.*`, `httpx_sse.*`, `typer.*`. Type hints are mandatory.
- Ruff line-length 100; `RUF001-003` ignored — Cyrillic in strings/docs is intentional. Match the existing Russian in user-facing strings and docstrings.

## agent/ — architecture rules

- State lives in plain-Python classes; Textual is a thin render layer. One `Agent` per chat tab; `LLMClient`, `ToolRegistry`, `Config` are shared.
- `ContextBuilder.build_messages()` is the single point where the messages array is assembled.
- LLM access is a hand-rolled `httpx` + `httpx-sse` client: POST `{api_base}/chat/completions`, SSE streaming, retries (3×, 1s/2s/4s) only on 408/425/429/5xx/network errors. No openai SDK.
- `LongTermMemory`, `ToolRegistry`, `McpAdapter`, `KnowledgeBase` are protocol stubs by design — don't implement them without a spec change.
- Model ids are `provider:model`, validated against the providers in `config.json`.

## Secrets / local files

- `agent/config.json` (real API keys) and `agent/sessions/` (SQLite + jsonl exports) are gitignored — never commit, never print their contents.
- `day-0X` projects load `.env` via python-dotenv (`API_BASE_URL`, `API_KEY`, `API_MODEL`; day-04 also `API_TEMPERATURE`); copy `.env.example`, run `uv sync` first.

## Verification

No CI. Verification = the three QA commands above, run from `agent/`.
