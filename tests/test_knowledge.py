"""Тесты KnowledgeBase: индексация, чанкинг, семантический поиск.

Эмбеддинги мокаются (FakeEmbedder) — реальный Ollama не трогается.
"""

from __future__ import annotations

from pathlib import Path

from agent.core.knowledge import (
    KnowledgeBase,
    _chunk_fixed,
    _chunk_paragraph,
    cosine_similarity,
)
from agent.llm.embedding import EmbeddingError
from agent.memory.longterm import Chunk

_VOCAB = ["apple", "banana", "orange", "grape"]


class FakeEmbedder:
    """Детерминированный эмбеддер: вектор-признаки наличия слов словаря."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise EmbeddingError("эмбединги недоступны")
        return [self._vec(text) for text in texts]

    def _vec(self, text: str) -> list[float]:
        words = set(text.lower().split())
        return [1.0 if word in words else 0.0 for word in _VOCAB]


def _base(embedder: FakeEmbedder, tmp_path: Path, **kw: object) -> KnowledgeBase:
    return KnowledgeBase(
        embedder=embedder,  # type: ignore[arg-type]
        knowledge_dir=tmp_path,
        top_k=kw.get("top_k", 4),  # type: ignore[arg-type]
        chunk_strategy=kw.get("chunk_strategy", "paragraph"),  # type: ignore[arg-type]
    )


async def test_rebuild_and_search(tmp_path) -> None:
    (tmp_path / "fruit.md").write_text(
        "Яблоки — красный фрукт.\n\nЯблочный пирог очень вкусный.", encoding="utf-8"
    )
    (tmp_path / "other.txt").write_text("Бананы жёлтые и длинные.", encoding="utf-8")
    kb = _base(FakeEmbedder(), tmp_path)
    assert kb.ready is False
    assert await kb.rebuild() is True
    assert kb.ready is True
    assert kb.size > 0
    results = await kb.search("apple")
    assert results
    assert all(isinstance(chunk, Chunk) for chunk in results)
    assert any("fruit.md" in (chunk.metadata.get("source") or "") for chunk in results)


async def test_search_top_k_limit(tmp_path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.md").write_text(f"apple chunk {i}", encoding="utf-8")
    kb = _base(FakeEmbedder(), tmp_path, top_k=3)
    await kb.rebuild()
    results = await kb.search("apple chunk")
    assert len(results) <= 3


async def test_search_returns_empty_in_degrades(tmp_path) -> None:
    embedder = FakeEmbedder(fail=True)
    (tmp_path / "a.md").write_text("apple", encoding="utf-8")
    kb = _base(embedder, tmp_path)
    assert await kb.rebuild() is False
    assert kb.size == 0
    assert await kb.search("apple") == []


async def test_empty_knowledge_dir(tmp_path) -> None:
    kb = _base(FakeEmbedder(), tmp_path)
    assert await kb.rebuild() is True
    assert kb.size == 0
    assert await kb.search("apple") == []


async def test_search_ignores_query_when_empty(tmp_path) -> None:
    kb = _base(FakeEmbedder(), tmp_path)
    assert await kb.search("") == []


def test_paragraph_chunking() -> None:
    text = "Заголовок первого абзаца. " * 10
    text += "\n\n" + "Короткий." + "\n\n" + "Третий длинный абзац. " * 5
    chunks = _chunk_paragraph(text)
    assert len(chunks) >= 1
    joined = "".join(chunks)
    assert "Заголовок" in joined
    assert "Короткий" in joined
    assert "Третий" in joined
    assert all(c for c in chunks)


def test_fixed_chunking_bounded_size() -> None:
    text = "абв" * 200
    chunks = _chunk_fixed(text, size=8, overlap=2)
    assert all(len(c) <= 8 for c in chunks)
    assert chunks  # непусто


def test_cosine_similarity() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([0, 0], [0, 0]) == 0.0


async def test_rebuild_batches_and_tracks_progress(tmp_path) -> None:
    """При батч-индексации несколько POST и прогресс доходит до total."""
    paragraphs = "\n\n".join(f"Абзац номер {i}. " + "Текст." * 30 for i in range(6))
    (tmp_path / "big.md").write_text(paragraphs, encoding="utf-8")
    embedder = FakeEmbedder()
    kb = KnowledgeBase(embedder=embedder, knowledge_dir=tmp_path, embed_batch_size=2)  # type: ignore[arg-type]
    assert await kb.rebuild() is True
    assert embedder.calls > 1  # порциями, а не одним запросом
    assert kb.indexing is False
    assert kb.indexed == kb.total
    assert kb.total == kb.size
    assert kb.size > 0


async def test_search_empty_when_disabled(tmp_path) -> None:
    (tmp_path / "a.md").write_text("apple банан", encoding="utf-8")
    kb = _base(FakeEmbedder(), tmp_path)
    await kb.rebuild()
    assert await kb.search("apple")
    kb.set_config(enabled=False)
    assert await kb.search("apple") == []
    kb.set_config(enabled=True)
    assert await kb.search("apple")


async def test_set_config_changes_strategy(tmp_path) -> None:
    text = "Яблоки красные. " * 20 + "\n\n" + "Бананы жёлтые. " * 20
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    kb = _base(FakeEmbedder(), tmp_path)
    assert kb.chunk_strategy == "paragraph"
    kb.set_config(chunk_strategy="fixed", chunk_size=30, chunk_overlap=5)
    assert kb.chunk_strategy == "fixed"
    assert kb.chunk_size == 30
    assert kb.chunk_overlap == 5
    assert await kb.rebuild() is True
    assert kb.size > 0
    results = await kb.search("apple")
    assert all(len(chunk.text) <= 30 for chunk in results)

