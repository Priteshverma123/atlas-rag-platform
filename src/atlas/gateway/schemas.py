"""OpenAI-compatible wire schemas + the gateway's provider-neutral internal types.

Clients speak the OpenAI Chat Completions dialect (any SDK works). Internally every provider
adapter consumes ``ProviderRequest`` and produces ``Completion`` / ``StreamEvent``, so routing,
caching and accounting never depend on a vendor's response shape.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- wire (OpenAI dialect)


class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON string, as in the OpenAI API


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: ToolCallFunction


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            return "".join(p.get("text", "") for p in self.content if p.get("type") == "text")
        return ""


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = Field(description="Route name (e.g. 'answer') or explicit 'provider/model'")
    messages: list[ChatMessage]
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    temperature: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    stream: bool = False
    user: str | None = None
    metadata: dict[str, str] | None = None

    def output_limit(self, default: int = 1024) -> int:
        return self.max_completion_tokens or self.max_tokens or default


class Usage(BaseModel):
    """Non-overlapping token buckets: ``prompt_tokens`` EXCLUDES cached tokens.

    Providers differ (OpenAI counts cached tokens inside prompt_tokens, Anthropic does not); the
    adapters normalise so cost = sum(bucket * price) never double counts.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.prompt_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.total_input + self.completion_tokens


class Choice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str | None = "stop"


class ChatResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex}")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)
    # gateway extensions (ignored by OpenAI SDKs)
    provider: str | None = None
    cost_usd: float | None = None
    cache: str | None = None


# --------------------------------------------------------------------------- internal


class ErrorKind(StrEnum):
    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    CONTEXT_WINDOW = "context_window"
    CONTENT_POLICY = "content_policy"
    BAD_REQUEST = "bad_request"
    AUTH = "auth"
    UNKNOWN = "unknown"

    @property
    def retryable(self) -> bool:
        return self in {ErrorKind.RATE_LIMIT, ErrorKind.OVERLOADED, ErrorKind.TIMEOUT,
                        ErrorKind.CONNECTION, ErrorKind.UNKNOWN}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.status = status
        self.retry_after = retry_after


@dataclass(slots=True)
class ProviderRequest:
    model: str
    messages: list[ChatMessage]
    max_tokens: int
    temperature: float | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    timeout_s: float = 60.0
    ttft_timeout_s: float = 10.0
    cache_prompt: bool = True  # place provider prompt-cache breakpoints where supported
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Completion:
    text: str
    model: str
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    tool_calls: list[ToolCall] | None = None


@dataclass(slots=True)
class StreamEvent:
    """Either a text delta, a terminal event carrying usage/finish reason, or both."""

    delta: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None
    tool_calls: list[ToolCall] | None = None
