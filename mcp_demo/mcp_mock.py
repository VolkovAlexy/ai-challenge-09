"""MCP-сервер (`mock_math`) поверх mock REST API.

Регистрирует инструменты `add` и `hello`. При вызове каждый инструмент ходит по
HTTP в mock API (`mcp_demo.mock_api`) и возвращает результат — это
демонстрация, как MCP оборачивает сторонний API. Агент подключается к этому
MCP-серверу через `McpAdapter` (stdio или streamable-http).

Запуск mock API:
    uv run agent-mock-api --port 8765

Запуск MCP-сервера над ним:
    uv run agent-mcp-mock --api-base http://127.0.0.1:8765          # stdio (спавнится McpAdapter)
    uv run agent-mcp-mock --http --api-base http://127.0.0.1:8765 \
        --port 8899                                                 # streamable-http

`--api-base` — адрес mock API, без него MCP-сервер не сможет выполнить
инструменты. Для `transport="http"` в конфиге url указывает сюда
(`http://127.0.0.1:8899/mcp`), а не на mock API. Сервер и приложение создаются
фабриками `make_server()`/`make_http_app()` — каждое подключение с чистым
состоянием (сессии MCP не переживают перезапуск).
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import Field
from starlette.applications import Starlette

DEFAULT_API_BASE = "http://127.0.0.1:8765"
DEFAULT_HTTP_PORT = 8899


def make_server(api_base: str) -> MCPServer:
    """Создаёт MCP-сервер, инструменты которого вызывают mock API по HTTP."""
    api_base = api_base.rstrip("/")
    server = MCPServer(
        "mock_math",
        version="0.1.0",
        instructions="MCP-сервер над mock API: арифметика и приветствие.",
    )

    @server.tool()
    async def add(
        a: Annotated[float, Field(description="Первое слагаемое")],
        b: Annotated[float, Field(description="Второе слагаемое")],
    ) -> str:
        """Складывает два числа через mock API и возвращает сумму."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{api_base}/add", json={"a": a, "b": b}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        return str(data["result"])

    @server.tool()
    async def hello(name: Annotated[str, Field(description="Имя пользователя")]) -> str:
        """Приветствует пользователя через mock API."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{api_base}/hello", json={"name": name}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        return str(data["message"])

    return server


def make_http_app(api_base: str) -> Starlette:
    """Streamable HTTP ASGI-приложение MCP-сервера над mock API."""
    return make_server(api_base).streamable_http_app()


def main(argv: list[str] | None = None) -> int:
    """Точка входа консольного скрипта `agent-mcp-mock`.

    По умолчанию — stdio-транспорт (для `transport="stdio"` в конфиге).
    С `--http` — Streamable HTTP на `--port` (по умолчанию 8899).
    """
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="MCP-сервер над mock API")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="адрес mock API")
    parser.add_argument("--http", action="store_true", help="запустить Streamable HTTP")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_HTTP_PORT, help="HTTP-порт (только для --http)"
    )
    args = parser.parse_args(argv)

    if args.http:
        uvicorn.run(
            make_http_app(args.api_base), host="127.0.0.1", port=args.port, log_level="info"
        )
        return 0

    asyncio.run(make_server(args.api_base).run_stdio_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
