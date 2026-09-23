"""Mock REST API-сервер — отдельный HTTP-сервис, поверх которого построен MCP-сервер.

Это «мок api» для демонстрации цепочки агент -> MCP -> API. Сервер отдаёт JSON
по REST-эндпоинтам `/health`, `/add` и `/hello`. Сам MCP-инструментарий здесь
отсутствует — их регистрирует `mcp_demo.mcp_mock`, который ходит в этот API
по HTTP при каждом вызове инструмента.

Запуск:
    uv run agent-mock-api --port 8765
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

DEFAULT_API_PORT = 8765


class AddRequest(BaseModel):
    """Тело запроса на сложение двух чисел."""

    a: float
    b: float


class HelloRequest(BaseModel):
    """Тело запроса приветствия пользователя."""

    name: str


def make_app() -> FastAPI:
    """Фабрика mock API-приложения (свежий инстанс на каждый запуск/тест)."""
    app = FastAPI(title="Mock API", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Проверка живости сервиса."""
        return {"status": "ok"}

    @app.post("/add")
    def add(req: AddRequest) -> dict[str, str]:
        """Складывает два числа и возвращает сумму строкой."""
        total = req.a + req.b
        result = str(int(total)) if total.is_integer() else str(total)
        return {"result": result}

    @app.post("/hello")
    def hello(req: HelloRequest) -> dict[str, str]:
        """Возвращает приветствие для пользователя."""
        return {"message": f"Привет, {req.name}!"}

    return app


app: FastAPI = make_app()


def main(argv: list[str] | None = None) -> int:
    """Точка входа `agent-mock-api`: поднимает mock API на `--port`."""
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Mock REST API-сервер")
    parser.add_argument("--host", default="127.0.0.1", help="интерфейс")
    parser.add_argument("--port", type=int, default=DEFAULT_API_PORT, help="HTTP-порт")
    args = parser.parse_args(argv)

    uvicorn.run(make_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
