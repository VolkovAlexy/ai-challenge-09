"""Демо MCP: подключается к сконфигурированным серверам и печатает списки инструментов.

Запуск: `uv run agent-mcp` (или `uv run python -m agent.tools.mcp_demo`).
Серверы берутся из `config.json` (секция `mcp_servers`).
"""

from __future__ import annotations

import asyncio

from agent.config.store import load_config
from agent.tools.mcp import McpAdapter


async def _run() -> int:
    """Подключиться к каждому MCP-серверу из конфига и вывести его инструменты."""
    config = load_config()
    if not config.mcp_servers:
        print("В config.json не настроен ни один mcp_servers.")
        return 0
    for name, spec in config.mcp_servers.items():
        adapter = McpAdapter(name)
        print(f"Подключение к '{name}' ({spec.transport})…")
        try:
            await adapter.connect(spec)
            tools = await adapter.list_tools()
            print(f"  инструментов: {len(tools)}")
            for tool in tools:
                print(f"  - {tool['name']}: {tool['description']}")
        except Exception as exc:
            print(f"  ошибка подключения: {exc}")
        finally:
            await adapter.close()
    return 0


def main() -> int:
    """Синхронная точка входа консольного скрипта `agent-mcp`."""
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
