"""Эмбединг-клиент: POST {api_base}/embeddings (OpenAI-совместимый, Ollama).

Используется KnowledgeBase для индексации файлов знаний и эмбеддинга запроса
при RAG-поиске. Не stream — один батч за вызов. Ошибка доступа/модели
не роняет ход агента: вызывающий (KnowledgeBase) деградирует к пустому
результату.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from agent.llm.client import _RETRYABLE_STATUS, RETRY_DELAYS


class EmbeddingError(Exception):
    """Ошибка обращения к эмбединг-эндпоинту (RAG деградирует)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Embedder:
    """Асинхронный клиент эмбедингов для одной модели/провайдера.

    В отличие от LLMClient, параметры коннекта (api_base/api_key/model)
    фиксируются на инстанс: KnowledgeBase работает с одной эмбединг-моделью.
    """

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str = "",
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={"Content-Type": "application/json"},
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинг батча текстов в порядке входного списка."""
        if not texts:
            return []
        body: dict[str, Any] = {"model": self._model, "input": texts}
        url = f"{self._api_base}/embeddings"
        attempt = 0
        while True:
            try:
                response = await self._client.post(url, json=body, headers=self._headers())
            except httpx.TransportError as exc:
                if attempt >= len(RETRY_DELAYS):
                    raise EmbeddingError(f"сетевая ошибка эмбедингов: {exc}") from exc
                attempt += 1
                await asyncio.sleep(RETRY_DELAYS[attempt - 1])
                continue
            if response.status_code >= 400:
                detail = self._detail(response)
                if response.status_code in _RETRYABLE_STATUS and attempt < len(RETRY_DELAYS):
                    attempt += 1
                    await asyncio.sleep(RETRY_DELAYS[attempt - 1])
                    continue
                raise EmbeddingError(
                    f"HTTP {response.status_code}: {detail}", status=response.status_code
                )
            return self._parse(response.json())

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
            if isinstance(error, str) and error:
                return error
        except (ValueError, AttributeError):
            pass
        return response.text[:300] or "без деталей"

    @staticmethod
    def _parse(payload: Any) -> list[list[float]]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise EmbeddingError("эмбединг-эндпоинт не вернул data")
        vectors: list[list[float]] = []
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise EmbeddingError("некорректный элемент эмбедингов в ответе")
            vectors.append([float(v) for v in item["embedding"]])
        return vectors
