import json
from collections.abc import Iterator
from typing import Any

import httpx

from .config import Settings
from .errors import APIError, AuthError, ConnectionError, RateLimitError
from .models import ChatRequest, ChatResponse, Message

STREAM_DONE = "[DONE]"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)


class APIClient:
    """Тонкий клиент для OpenAI-compatible /chat/completions поверх httpx."""

    def __init__(self, settings: Settings):
        self.settings = settings
        headers = {"Content-Type": "application/json"}
        if settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"
        self._http = httpx.Client(
            base_url=settings.base_url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "APIClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _build_messages(self, prompt: str, system: str | None = None) -> list[Message]:
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt))
        return messages

    def complete(
        self, prompt: str, system: str | None = None, stop: list[str] | None = None
    ) -> ChatResponse:
        """Полный (не стриминговый) ответ чата."""
        request = ChatRequest(
            model=self.settings.model,
            messages=self._build_messages(prompt, system),
            stream=False,
            stop=stop,
        )
        try:
            with self._http.stream(
                "POST", self.settings.chat_path, json=request.to_dict()
            ) as response:
                self._raise_for_status(response)
                data = json.loads(response.read())
        except httpx.HTTPError as exc:
            raise self._connection_error(exc) from exc
        return ChatResponse.from_json(data)

    def stream(
        self, prompt: str, system: str | None = None, stop: list[str] | None = None
    ) -> Iterator[str]:
        """Стриминговая генерация: поэтапно отдаёт фрагменты текста."""
        request = ChatRequest(
            model=self.settings.model,
            messages=self._build_messages(prompt, system),
            stream=True,
            stop=stop,
        )
        try:
            with self._http.stream(
                "POST", self.settings.chat_path, json=request.to_dict()
            ) as response:
                self._raise_for_status(response)
                yield from self._iter_delta_text(response)
        except httpx.HTTPError as exc:
            raise self._connection_error(exc) from exc

    def _iter_delta_text(self, response: httpx.Response) -> Iterator[str]:
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == STREAM_DONE:
                return
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            choices = event.get("choices") or []
            for choice in choices:
                delta = choice.get("delta") or {}
                text = delta.get("content")
                if text:
                    yield text

    @staticmethod
    def _connection_error(exc: httpx.HTTPError) -> ConnectionError:
        return ConnectionError(f"Ошибка соединения с API: {exc}")

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        body = response.text
        try:
            message = json.loads(body)["error"]["message"]
        except (json.JSONDecodeError, KeyError, TypeError):
            message = body or httpx.codes.get_reason_phrase(response.status_code)
        exc_cls = (
            AuthError
            if response.status_code in (401, 403)
            else RateLimitError
            if response.status_code == 429
            else APIError
        )
        raise exc_cls(message, status_code=response.status_code, body=body)


__all__ = ["APIClient"]
