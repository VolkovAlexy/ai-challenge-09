"""Долговременная память: markdown-файл, общий для всех агентов процесса.

Хранит накопленные знания о пользователе, предпочтения и глобальные паттерны
поведения (LONGTERM_MEMORY.md рядом с SYSTEM_PROMPT.md). Содержимое целиком
впрыскивается в контекст каждого запроса как расширение системного промпта —
никакого поиска/rankинга: объём мал, запись только явная (действие «запомнить»
в UI либо принятое предложение модели).

Формат файла: заголовок + по одной записи на строку-буллет («- текст»).
Запись — атомарная (tmp + replace), чтение устойчиво к отсутствию файла.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from agent.core.message import Message

LONGTERM_HEADER = "Долговременная память (сохранённые знания, общие для всех сессий):"
LONGTERM_INSTRUCTION = (
    "Если в диалоге обнаружен устойчивый паттерн поведения пользователя или важное "
    "долговременное знание, предложи сохранить его: добавь в самый конец ответа блок "
    "[MEMORY_SUGGESTION]текст знания[/MEMORY_SUGGESTION]. Предлагай только существенное "
    "и не более одного блока за ответ; не повторяй уже сохранённые записи. Если новое "
    "знание противоречит уже сохранённой записи, скажи об этом в ответе и предложи новую "
    "формулировку — устаревшую запись пользователь исправит в панели памяти."
)

ENTRY_PREFIX = "- "
FILE_HEADER = """# Долговременная память

Знания об пользователе, предпочтения и глобальные паттерны поведения.
Одна запись — одна строка; редактируется вручную или через интерфейс агента.
"""


class Chunk:
    """Чанк памяти/знаний: текст + метаданные (используется KnowledgeBase)."""

    def __init__(self, text: str, metadata: dict[str, str] | None = None) -> None:
        self.text = text
        self.metadata = metadata or {}


class LongTermMemory:
    """Долговременная память в markdown-файле (общая на всех агентов процесса)."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> str:
        """Содержимое памяти; пустая строка, если файла нет."""
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def entries(self) -> list[str]:
        """Список записей (строк-буллетов без префикса), по порядку."""
        result: list[str] = []
        for line in self.load().splitlines():
            stripped = line.strip()
            if stripped.startswith(ENTRY_PREFIX):
                result.append(stripped[len(ENTRY_PREFIX) :])
        return result

    def append(self, text: str) -> str:
        """Добавляет запись (одна строка). Возвращает нормализованный текст записи."""
        entry = " ".join(text.split())
        if not entry:
            raise ValueError("запись памяти не может быть пустой")
        lines = self._body_lines()
        lines.append(ENTRY_PREFIX + entry)
        self._write(lines)
        return entry

    def remove(self, index: int) -> str:
        """Удаляет запись по индексу (0-based, порядок entries()). Возвращает текст."""
        entries = self.entries()
        if index < 0 or index >= len(entries):
            raise IndexError(f"записи памяти с индексом {index} нет")
        removed = entries[index]
        prefix = ENTRY_PREFIX + removed
        lines = [line for line in self._body_lines() if line.strip() != prefix]
        self._write(lines)
        return removed

    def update(self, index: int, text: str) -> str:
        """Заменяет запись по индексу; возвращает новый текст записи.

        Нормализация та же, что в append (схлопывание пробелов, одна строка).
        """
        entry = " ".join(text.split())
        if not entry:
            raise ValueError("запись памяти не может быть пустой")
        entries = self.entries()
        if index < 0 or index >= len(entries):
            raise IndexError(f"записи памяти с индексом {index} нет")
        entries[index] = entry
        self._write([ENTRY_PREFIX + e for e in entries])
        return entry

    def _body_lines(self) -> list[str]:
        """Строки файла без заголовка-шаблона (заголовок пересоздаётся при записи).

        Заголовок отрезается один раз: при повторной записи файл не должен
        аккумулировать копии шаблона (иначе каждая запись дублирует заголовок).
        """
        content = self.load()
        if content.startswith(FILE_HEADER):
            content = content[len(FILE_HEADER) :]
        return [line for line in content.splitlines() if line.strip()]

    def _write(self, body_lines: list[str]) -> None:
        """Атомарная запись: заголовок + тело, через tmp-файл + replace."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = FILE_HEADER + "\n" + "\n".join(body_lines)
        if body_lines:
            content += "\n"
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".longterm-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(content)
            os.replace(tmp, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    async def recall(self, query: str) -> list[str]:
        """Задел под точечный recall; сейчас память всегда целиком в контексте."""
        return self.entries() if query.strip() else []

    async def store(self, messages: list[Message]) -> None:
        """Stub: авто-сохранение диалога не делаем — запись только явная."""
        return None
