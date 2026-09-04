class ClientError(Exception):
    """Базовое исключение клиента."""


class ConfigError(ClientError):
    """Ошибка конфигурации (нет ключа, некорректный URL и т.п.)."""


class APIError(ClientError):
    """Ошибка со стороны API: HTTP-статус, несуществующая модель и т.п."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AuthError(APIError):
    """Ошибка аутентификации/доступа."""


class RateLimitError(APIError):
    """Превышение лимита запросов."""


class ConnectionError(ClientError):
    """Проблемы с сетью."""


__all__ = [
    "APIError",
    "AuthError",
    "ClientError",
    "ConfigError",
    "ConnectionError",
    "RateLimitError",
]
