"""Долговременная память: записи уровня проекта в SQL (таблица longterm_entries).

Хранит накопленные знания о пользователе, предпочтения и глобальные паттерны
поведения. Память общая на все сессии проекта; содержимое целиком впрыскивается
в контекст каждого запроса как расширение системного промпта — никакого
поиска/rankинга: объём мал, запись только явная (действие «запомнить» в UI либо
принятое предложение модели).
"""

from __future__ import annotations

from typing import Protocol

from agent.memory.persistence import SessionStore

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


class LongTermSource(Protocol):
    """Общий интерфейс долгосрочной памяти (SQL-проект, see ProjectLongTermMemory).

    Agent читает память только через `load()`; state/web — через
    `entries()/append()/remove()/update()`.
    """

    def load(self) -> str: ...

    def entries(self) -> list[str]: ...

    def append(self, text: str) -> str: ...

    def remove(self, index: int) -> str: ...

    def update(self, index: int, text: str) -> str: ...


class Chunk:
    """Чанк памяти/знаний: текст + метаданные (используется KnowledgeBase)."""

    def __init__(self, text: str, metadata: dict[str, str] | None = None) -> None:
        self.text = text
        self.metadata = metadata or {}


class ProjectLongTermMemory:
    """Долгосрочная память проекта: записи в SQL (таблица longterm_entries).

    Память уровня проекта (не сессии): видна всем сессиям проекта и
    впрыскивается в контекст. Формат `load()` воспроизводит
    FILE_HEADER + буллеты, чтобы инжект был идентичным.
    """

    def __init__(self, store: SessionStore, project_id: str) -> None:
        self._store = store
        self._project_id = project_id

    @property
    def project_id(self) -> str:
        return self._project_id

    def load(self) -> str:
        """Содержимое памяти (заголовок + записи); пусто, если записей нет."""
        body = "\n".join(ENTRY_PREFIX + entry for entry in self.entries())
        if not body:
            return ""
        return FILE_HEADER + "\n" + body + "\n"

    def entries(self) -> list[str]:
        return self._store.list_longterm(self._project_id)

    def append(self, text: str) -> str:
        return self._store.append_longterm(self._project_id, text)

    def remove(self, index: int) -> str:
        return self._store.remove_longterm(self._project_id, index)

    def update(self, index: int, text: str) -> str:
        return self._store.update_longterm(self._project_id, index, text)
