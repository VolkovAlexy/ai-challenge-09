"""Тесты разбора RAG-ответа: parse_rag_answer.

Проверяем вычленение разделов «Источники:» и «Цитаты:» из ответа модели
и маппинг номеров [N] обратно на инжектированные чанки.
"""

from __future__ import annotations

from agent.core.rag import RagChunk, parse_rag_answer

_CHUNKS = [
    RagChunk(
        label=1,
        source="knowledge/messages/chunk_001.txt",
        chunk_id="1",
        text="Яблоки красные.",
    ),
    RagChunk(
        label=2,
        source="knowledge/messages/chunk_002.txt",
        chunk_id="3",
        text="Бананы жёлтые.",
    ),
]


def test_parse_full_answer() -> None:
    text = (
        "Яблоки бывают красными.\n\n"
        "**Источники:**\n"
        "- [1] источник: knowledge/messages/chunk_001.txt (chunk_id: 1)\n"
        "\n"
        "**Цитаты:**\n"
        "- [1] «Яблоки красные» — подтверждает цвет.\n"
        "- [2] «Бананы жёлтые» — второй факт."
    )
    result = parse_rag_answer(text, _CHUNKS)
    assert result.answer == "Яблоки бывают красными."
    assert len(result.sources) == 2
    assert result.sources[0].ref == 1
    assert result.sources[0].source == "knowledge/messages/chunk_001.txt"
    assert result.sources[0].chunk_id == "1"
    assert result.sources[1].ref == 2
    assert len(result.quotes) == 2
    assert result.quotes[0].ref == 1
    assert result.quotes[0].text == "«Яблоки красные» — подтверждает цвет."
    assert result.quotes[1].chunk_id == "3"


def test_parse_without_sections_keeps_whole_text() -> None:
    text = "Просто ответ без разделов."
    result = parse_rag_answer(text, _CHUNKS)
    assert result.answer == text
    assert result.sources == []
    assert result.quotes == []


def test_parse_sources_only() -> None:
    text = (
        "Ответ.\n\n"
        "Источники:\n"
        "- [1] источник: chunk_001.txt"
    )
    result = parse_rag_answer(text, _CHUNKS)
    assert result.answer == "Ответ."
    assert len(result.sources) == 1
    assert result.sources[0].ref == 1
    assert result.quotes == []


def test_parse_unmapped_refs_leave_blank_address() -> None:
    text = (
        "Ответ.\n\n"
        "Источники:\n- [9] источник: неизвестный.txt\n"
        "Цитаты:\n- [9] фрагмент."
    )
    result = parse_rag_answer(text, _CHUNKS)
    assert len(result.sources) == 1
    assert result.sources[0].ref == 9
    assert result.sources[0].source == ""
    assert result.sources[0].chunk_id == ""
    assert len(result.quotes) == 1
    assert result.quotes[0].source == ""
    assert result.quotes[0].chunk_id == ""


def test_parse_with_no_chunks_still_returns_answer() -> None:
    text = "Ответ.\n\nИсточники:\n- [1] источник: x.txt"
    result = parse_rag_answer(text, None)
    assert result.answer == "Ответ."
    assert result.sources[0].source == ""
