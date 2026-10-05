"""Структурированный RAG: метаданные чанка и разбор ответа модели.

Чанк, инжектируемый в контекст (`RagChunk`), несёт адрес источника (source +
chunk_id), чтобы модель могла на него сослаться. После ответа модель обязана
привести разделы «Источники:» и «Цитаты:»; `parse_rag_answer` вычленяет их в
структуру (`RagAnswer`) для DTO/UI, оставляя чистый ответ и списки цитат.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class RagChunk:
    """Один инжектируемый чанк знаний: метки + адрес источника."""

    label: int  # номер [N], которым модель ссылается на источник
    source: str  # путь к файлу-источнику
    chunk_id: str  # идентификатор чанка внутри источника
    text: str  # фрагмент текста


class RagSource(BaseModel):
    """Источник, использованный моделью (source + chunk_id)."""

    ref: int  # номер [N], которым модель сослалась на источник
    source: str
    chunk_id: str


class RagQuote(BaseModel):
    """Цитата из найденного чанка (дословный фрагмент)."""

    ref: int
    source: str
    chunk_id: str
    text: str


class RagAnswer(BaseModel):
    """Разобранный RAG-ответ: чистый ответ + использованные источники + цитаты."""

    answer: str
    sources: list[RagSource]
    quotes: list[RagQuote]


_SOURCES_HEADER_RE = re.compile(
    r"(?m)^\s*(?:[-*#]{1,3}\s*)?(?:\*\*)?Источники(?:\*\*)?\s*:?", re.IGNORECASE
)
_QUOTES_HEADER_RE = re.compile(
    r"(?m)^\s*(?:[-*#]{1,3}\s*)?(?:\*\*)?Цитаты(?:\*\*)?\s*:?", re.IGNORECASE
)
_REF_RE = re.compile(r"\[\s*(\d+)\s*\]")
_QUOTE_LINE_RE = re.compile(r"^\s*(?:[-*>\s]*)?\[\s*(\d+)\s*\]\s*(.+)$")


def _section_bounds(text: str) -> tuple[int, int]:
    """Индексы: (начало «Источники», начало «Цитаты»); -1 — раздела нет."""
    sources = _SOURCES_HEADER_RE.search(text)
    sources_idx = sources.start() if sources else -1
    if sources_idx < 0:
        return -1, -1
    quotes = _QUOTES_HEADER_RE.search(text, sources_idx + 1)
    quotes_idx = quotes.start() if quotes else -1
    return sources_idx, quotes_idx


def parse_rag_answer(text: str, chunks: list[RagChunk] | None = None) -> RagAnswer:
    """Вычленяет источники и цитаты из ответа модели.

    `chunks` — чанки, инжектированные в контекст: используются для маппинга
    номера [N] → (source, chunk_id). Если разделов нет / чанков нет — возвращает
    весь текст как ответ со пустыми списками (деградация не ломает ход).
    """
    by_ref = {chunk.label: chunk for chunk in (chunks or [])}
    sources_idx, quotes_idx = _section_bounds(text)
    if sources_idx < 0:
        return RagAnswer(answer=text.strip(), sources=[], quotes=[])

    answer = text[:sources_idx].strip()
    if quotes_idx >= 0:
        sources_body = text[sources_idx:quotes_idx]
        quotes_body = text[quotes_idx:]
    else:
        sources_body = text[sources_idx:]
        quotes_body = ""

    refs: set[int] = set()
    for line in sources_body.splitlines():
        refs.update(int(m.group(1)) for m in _REF_RE.finditer(line))

    quotes: list[RagQuote] = []
    quote_refs: set[int] = set()
    for line in quotes_body.splitlines():
        m = _QUOTE_LINE_RE.match(line)
        if m:
            ref = int(m.group(1))
            quote_refs.add(ref)
            chunk = by_ref.get(ref)
            quotes.append(
                RagQuote(
                    ref=ref,
                    source=chunk.source if chunk else "",
                    chunk_id=chunk.chunk_id if chunk else "",
                    text=m.group(2).strip(),
                )
            )
    refs |= quote_refs

    sources: list[RagSource] = []
    for ref in sorted(refs):
        chunk = by_ref.get(ref)
        sources.append(
            RagSource(
                ref=ref,
                source=chunk.source if chunk else "",
                chunk_id=chunk.chunk_id if chunk else "",
            )
        )
    return RagAnswer(answer=answer, sources=sources, quotes=quotes)
