"""HTTP-бэкенд для web-интерфейса: FastAPI поверх ядра agent.

Обёртки над ядром: реестр агентов (WebState), DTO-схемы (dto.py),
SSE-стриминг (stream.py) и фабрика приложения (app.py).
"""

from agent.web_server.app import create_app, main

__all__ = ["create_app", "main"]
