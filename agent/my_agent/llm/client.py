"""Тонкий OpenAI-compatible клиент: POST /chat/completions, SSE-стриминг, ретраи.

Клиент общий для всех агентов; параметры коннекта (api_base, api_key)
передаются per-запрос — агент резолвит их из id модели 'provider:model'.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from httpx_sse import SSEError, aconnect_sse

from my_agent.core.message import ChatChunk, ChatRequest

RETRY_DELAYS = (1.0, 2.0, 4.0)
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class LLMError(Exception):
    """Ошибка обращения к LLM (показывается в чате, чат продолжается)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMClient:
    """Асинхронный клиент. Общий инстанс; per-запросные api_base/api_key."""

    def __init__(self, timeout: float = 120.0) -> None:
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def astream(
        self,
        request: ChatRequest,
        api_base: str,
        api_key: str = "",
    ) -> AsyncIterator[ChatChunk]:
        """Стриминг chat/completions.

        Ретраи (3×, backoff 1s/2s/4s) — только на 429/5xx/сетевые ошибки
        и только до получения первого чанка (повтор запроса после частичного
        вывода вызвал бы дублирование токенов). 400/401/прочие 4xx —
        LLMError сразу, без ретраев.
        """
        url = f"{api_base.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        body = request.to_body()

        attempt = 0
        while True:
            try:
                async with aconnect_sse(
                    self._client, "POST", url, json=body, headers=headers
                ) as event_source:
                    response = event_source.response
                    if response.status_code >= 400:
                        await response.aread()
                        detail = self._error_detail(response)
                        if response.status_code in _RETRYABLE_STATUS:
                            raise _RetryableHTTPError(response.status_code, detail)
                        raise LLMError(
                            f"HTTP {response.status_code}: {detail}",
                            status=response.status_code,
                        )
                    async for event in event_source.aiter_sse():
                        chunk = self._parse_event(event.data)
                        if chunk is not None:
                            yield chunk
                return
            except _RetryableHTTPError as exc:
                if attempt >= len(RETRY_DELAYS):
                    raise LLMError(str(exc), status=exc.status) from exc
                attempt += 1
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])
            except (httpx.TransportError, SSEError) as exc:
                if attempt >= len(RETRY_DELAYS):
                    raise LLMError(f"сетевая ошибка: {exc}") from exc
                attempt += 1
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        try:
            payload = json.loads(response.text)
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
        except (json.JSONDecodeError, AttributeError):
            pass
        return response.text[:300] or "без деталей"

    @staticmethod
    def _parse_event(data: str) -> ChatChunk | None:
        data = data.strip()
        if not data or data == "[DONE]":
            return None
        try:
            payload: Any = json.loads(data)
        except json.JSONDecodeError as exc:
            raise LLMError(f"некорректный SSE-чанк: {data[:200]}") from exc
        return ChatChunk.from_sse_data(payload)


class _RetryableHTTPError(Exception):
    """Внутренний: ответ 429/5xx, подлежит ретраю."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
