"""KnowledgeBase — семантический поиск по файлам знаний (RAG).

Индекс строится в памяти на старте (или по запросу) из файлов папки
`knowledge_dir`: чанки нарезаются (абзацы или fixed-куски с перекрытием),
эмбеддятся через `Embedder`, хранятся вместе с исходным путём. `search()`
эмбеддит запрос и возвращает топ-k ближайших `Chunk` по косинусной мере.

Интеграционная точка: `ContextBuilder.build_messages(..., rag_chunks=...)`;
агент передаёт сюда результат `search()` текста сообщения пользователя.
Ошибки эмбеддингов деградируют к пустому результату (RAG не роняет ход).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from agent.config.schema import EmbeddingStrategy
from agent.llm.embedding import Embedder, EmbeddingError
from agent.memory.longterm import Chunk

_SUPPORTED_SUFFIXES = {".md", ".txt", ".markdown"}
_MIN_PARAGRAPH_LEN = 120


@dataclass
class _Entry:
    """Один проиндексированный чанк: текст, вектор, адрес источника."""

    text: str
    vector: list[float]
    source: str
    chunk_id: str


def _chunk_paragraph(text: str) -> list[str]:
    """Нарезка по абзацам (пустая строка — разделитель); короткие склеиваются."""
    chunks: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if chunks and len(chunks[-1]) < _MIN_PARAGRAPH_LEN:
            chunks[-1] = f"{chunks[-1]}\n\n{paragraph}"
        else:
            chunks.append(paragraph)
    return chunks


def _chunk_fixed(text: str, size: int, overlap: int) -> list[str]:
    """Непрерывные куски размером `size` символов с перекрытием `overlap`."""
    step = max(1, size - overlap)
    return [
        piece.strip()
        for i in range(0, len(text), step)
        if (piece := text[i : i + size]).strip()
    ]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Косинусная близость двух векторов (0.0 при нулевой норме)."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class KnowledgeBase:
    """Семантический поиск по файлам знаний (in-memory индекс)."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        knowledge_dir: Path,
        top_k: int = 4,
        retrieve_top_k: int = 10,
        relevance_enabled: bool = False,
        relevance_threshold: float = 0.6,
        chunk_strategy: EmbeddingStrategy = "paragraph",
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        embed_batch_size: int = 128,
    ) -> None:
        self._embedder = embedder
        self._dir = knowledge_dir
        self._top_k = max(1, top_k)
        self._retrieve_top_k = max(1, retrieve_top_k)
        self._relevance_enabled = relevance_enabled
        self._relevance_threshold = max(0.0, relevance_threshold)
        self._chunk_strategy = chunk_strategy
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._embed_batch_size = max(1, embed_batch_size)
        self._entries: list[_Entry] = []
        self.ready = False
        # runtime-управление (эфемерно, из панели «Знания»)
        self.enabled = True
        # прогресс индексации (для поллинга UI)
        self.indexing = False
        self.indexed = 0
        self.total = 0
        self._error = False

    @property
    def size(self) -> int:
        """Число проиндексированных чанков."""
        return len(self._entries)

    @property
    def chunk_strategy(self) -> EmbeddingStrategy:
        return self._chunk_strategy

    @property
    def chunk_size(self) -> int:
        return self._chunk_size

    @property
    def chunk_overlap(self) -> int:
        return self._chunk_overlap

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def retrieve_top_k(self) -> int:
        return self._retrieve_top_k

    @property
    def relevance_enabled(self) -> bool:
        return self._relevance_enabled

    @property
    def relevance_threshold(self) -> float:
        return self._relevance_threshold

    @property
    def error(self) -> bool:
        """Последняя индексация завершилась ошибкой (сбор падает → RAG выключен)."""
        return self._error

    def set_config(
        self,
        *,
        enabled: bool | None = None,
        top_k: int | None = None,
        retrieve_top_k: int | None = None,
        relevance_enabled: bool | None = None,
        relevance_threshold: float | None = None,
        chunk_strategy: EmbeddingStrategy | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        """Эфемерно меняет runtime-настройки (без записи в config.json).

        Только мутация; перестройку индекса запускает вызывающий (WebState),
        если изменилась нарезка чанков.
        """
        if enabled is not None:
            self.enabled = enabled
        if top_k is not None:
            self._top_k = max(1, top_k)
        if retrieve_top_k is not None:
            self._retrieve_top_k = max(1, retrieve_top_k)
        if relevance_enabled is not None:
            self._relevance_enabled = relevance_enabled
        if relevance_threshold is not None:
            self._relevance_threshold = max(0.0, relevance_threshold)
        if chunk_strategy is not None:
            self._chunk_strategy = chunk_strategy
        if chunk_size is not None:
            self._chunk_size = max(1, chunk_size)
        if chunk_overlap is not None:
            self._chunk_overlap = max(0, chunk_overlap)

    async def close(self) -> None:
        """Закрывает эмбединг-клиент (делегируется Embedder, если его инстанс)."""
        await self._embedder.close()

    def _chunks(self, text: str) -> list[str]:
        if self._chunk_strategy == "fixed":
            return _chunk_fixed(text, self._chunk_size, self._chunk_overlap)
        return _chunk_paragraph(text)

    def _collect_chunks(self) -> list[tuple[str, str, str]]:
        """Читает файлы из папки, возвращает список (текст, источник, chunk_id).

        `chunk_id` — 1-based номер чанка внутри его файла-источника.
        """
        if not self._dir.is_dir():
            return []
        chunks: list[tuple[str, str, str]] = []
        for path in sorted(self._dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for idx, chunk in enumerate(self._chunks(text), start=1):
                if chunk:
                    chunks.append((chunk, str(path), str(idx)))
        return chunks

    async def rebuild(self) -> bool:
        """Переиндексирует папку знаний. True — индекс построен, False — деградация.

        Чанки эмбеддятся порциями `embed_batch_size` (один POST на порцию) — так
        большой корпус не упирается в таймаут одного запроса. Прогресс
        (`indexing`/`indexed`/`total`) обновляется после каждой порции.
        """
        collected = self._collect_chunks()
        self.total = len(collected)
        self.indexed = 0
        self.indexing = True
        self._error = False
        if not collected:
            self._entries = []
            self.ready = True
            self.indexing = False
            return True
        entries: list[_Entry] = []
        try:
            for start in range(0, len(collected), self._embed_batch_size):
                batch = collected[start : start + self._embed_batch_size]
                vectors = await self._embedder.embed_texts([text for text, _, _ in batch])
                entries.extend(
                    _Entry(text=text, vector=vector, source=source, chunk_id=chunk_id)
                    for (text, source, chunk_id), vector in zip(batch, vectors, strict=False)
                    if len(vector) > 0
                )
                self.indexed = min(start + len(batch), self.total)
        except EmbeddingError:
            self._entries = []
            self.ready = False
            self.indexing = False
            self._error = True
            return False
        self._entries = entries
        self.ready = True
        self.indexing = False
        return True

    async def search(self, query: str) -> list[Chunk]:
        """Топ-k релевантных чанков по запросу. → [] при ошибке/пустом индексе."""
        if not self.enabled or not self.ready or not self._entries or not query.strip():
            return []
        try:
            (query_vector,) = await self._embedder.embed_texts([query])
        except EmbeddingError:
            return []
        ranked = sorted(
            ((cosine_similarity(query_vector, entry.vector), entry) for entry in self._entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if self._relevance_enabled:
            candidates = ranked[: self._retrieve_top_k]
            candidates = [
                (score, entry) for score, entry in candidates if score >= self._relevance_threshold
            ]
            return [
                Chunk(
                    text=entry.text,
                    metadata={"source": entry.source, "chunk_id": entry.chunk_id},
                )
                for _, entry in candidates[: self._top_k]
            ]
        return [
            Chunk(
                text=entry.text,
                metadata={"source": entry.source, "chunk_id": entry.chunk_id},
            )
            for _, entry in ranked[: self._top_k]
        ]
