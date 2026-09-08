"""Модели сообщений OpenAI-compatible API.

С первого дня поддерживаются роли `tool` и `tool_calls` — исполнение
инструментов появится позже (ToolRegistry), но протокол уже зафиксирован.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FunctionCall(BaseModel):
    name: str
    arguments: str = "{}"  # JSON-строка (в стриме накапливается по кусочкам)


class ToolCall(BaseModel):
    id: str = ""
    type: Literal["function"] = "function"
    function: FunctionCall


class Message(BaseModel):
    role: Role
    content: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_api(self) -> dict[str, Any]:
        """Сериализация в dict формата OpenAI API (без полей со значениями None)."""
        data: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            data["content"] = self.content
        if self.name is not None:
            data["name"] = self.name
        if self.tool_calls is not None:
            data["tool_calls"] = [tc.model_dump(exclude_none=True) for tc in self.tool_calls]
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        return data


class ChatRequest(BaseModel):
    """Полный запрос к /chat/completions."""

    model: str
    messages: list[Message]
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int | None = None
    stop: list[str] | None = None
    stream: bool = True

    def to_body(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api() for m in self.messages],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "stream": self.stream,
        }
        if self.max_tokens is not None:
            data["max_tokens"] = self.max_tokens
        if self.stop:
            data["stop"] = self.stop
        return data


class ToolCallDelta(BaseModel):
    """Кусочек tool_call в стриминговом дельте (индексируется по index)."""

    index: int = 0
    id: str | None = None
    type: Literal["function"] | None = None
    function_name: str | None = None
    function_arguments: str | None = None


class ChatChunk(BaseModel):
    """Один стриминговый дельте, нормализованный из SSE-события."""

    content: str | None = None
    tool_call_deltas: list[ToolCallDelta] = Field(default_factory=list)
    finish_reason: str | None = None

    @classmethod
    def from_sse_data(cls, data: dict[str, Any]) -> ChatChunk | None:
        """Разбирает JSON одного SSE-события; None — для маркера [DONE]."""
        if not data:
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        delta = choices[0].get("delta") or {}
        tool_calls: list[ToolCallDelta] = []
        for raw in delta.get("tool_calls") or []:
            fn = raw.get("function") or {}
            tool_calls.append(
                ToolCallDelta(
                    index=int(raw.get("index", 0)),
                    id=raw.get("id"),
                    type=raw.get("type"),
                    function_name=fn.get("name"),
                    function_arguments=fn.get("arguments"),
                )
            )
        return cls(
            content=delta.get("content"),
            tool_call_deltas=tool_calls,
            finish_reason=choices[0].get("finish_reason"),
        )
