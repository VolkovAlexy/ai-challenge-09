from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatRequest:
    model: str
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    stop: list[str] | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [m.to_dict() for m in self.messages],
            "stream": self.stream,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        if self.stop:
            payload["stop"] = self.stop
        payload.update(self.extra)
        return payload


@dataclass
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class ChatResponse:
    content: str
    model: str
    usage: Usage | None = None
    finish_reason: str | None = None
    raw: dict | None = None

    @classmethod
    def from_json(cls, data: dict) -> "ChatResponse":
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        finish_reason = choice.get("finish_reason")
        usage_data = data.get("usage")
        usage = None
        if usage_data:
            usage = Usage(
                prompt_tokens=usage_data.get("prompt_tokens"),
                completion_tokens=usage_data.get("completion_tokens"),
                total_tokens=usage_data.get("total_tokens"),
            )
        return cls(
            content=content,
            model=data.get("model", ""),
            usage=usage,
            finish_reason=finish_reason,
            raw=data,
        )


__all__ = ["ChatRequest", "ChatResponse", "Message", "Usage"]
