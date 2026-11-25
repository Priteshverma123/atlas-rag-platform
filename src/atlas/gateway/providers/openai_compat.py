"""Adapter for every OpenAI-compatible endpoint: OpenAI, Google Gemini (OpenAI compatibility
layer), xAI Grok, Ollama, vLLM, and the local mock server.

One well-tested adapter instead of five near-duplicates. Trade-off (ADR-006): provider-specific
features not exposed through the compatibility layers (e.g. Gemini explicit context caching) are
out of scope.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from atlas.gateway.providers.base import Provider, first_with_timeout
from atlas.gateway.schemas import (
    Completion,
    ErrorKind,
    ProviderError,
    ProviderRequest,
    StreamEvent,
    ToolCall,
    ToolCallFunction,
    Usage,
)

_CONTEXT_MARKERS = ("context_length_exceeded", "maximum context length", "context window", "too many tokens")
_POLICY_MARKERS = ("content_filter", "content_policy", "safety", "responsible ai")


def _retry_after(exc: openai.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value else None
    except (ValueError, AttributeError):
        return None


def map_openai_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(ErrorKind.TIMEOUT, str(exc))
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(ErrorKind.CONNECTION, str(exc))
    if isinstance(exc, openai.RateLimitError):
        return ProviderError(ErrorKind.RATE_LIMIT, str(exc), status=429, retry_after=_retry_after(exc))
    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return ProviderError(ErrorKind.AUTH, str(exc), status=exc.status_code)
    if isinstance(exc, openai.BadRequestError):
        msg = str(exc).lower()
        if any(m in msg for m in _CONTEXT_MARKERS):
            return ProviderError(ErrorKind.CONTEXT_WINDOW, str(exc), status=400)
        if any(m in msg for m in _POLICY_MARKERS):
            return ProviderError(ErrorKind.CONTENT_POLICY, str(exc), status=400)
        return ProviderError(ErrorKind.BAD_REQUEST, str(exc), status=400)
    if isinstance(exc, openai.APIStatusError):
        kind = ErrorKind.OVERLOADED if exc.status_code in (502, 503, 504, 529) else ErrorKind.UNKNOWN
        return ProviderError(kind, str(exc), status=exc.status_code, retry_after=_retry_after(exc))
    return ProviderError(ErrorKind.UNKNOWN, repr(exc))


class OpenAICompatProvider(Provider):
    def __init__(self, name: str, base_url: str, api_key: str, *,
                 max_tokens_param: str = "max_tokens", supports_temperature: bool = True) -> None:
        self.name = name
        self.max_tokens_param = max_tokens_param
        self.supports_temperature = supports_temperature
        # max_retries=0: the router owns retries so attempts/cooldowns are accounted exactly once.
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def _kwargs(self, req: ProviderRequest) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": req.model,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            self.max_tokens_param: req.max_tokens,
            "timeout": req.timeout_s,
        }
        if req.temperature is not None and self.supports_temperature:
            kw["temperature"] = req.temperature
        if req.tools:
            kw["tools"] = req.tools
            if req.tool_choice is not None:
                kw["tool_choice"] = req.tool_choice
        if req.response_format:
            kw["response_format"] = req.response_format
        return kw

    @staticmethod
    def _usage(u: Any) -> Usage:
        if u is None:
            return Usage()
        cached = 0
        details = getattr(u, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        # OpenAI reports cached tokens *inside* prompt_tokens; subtract to keep buckets disjoint.
        return Usage(prompt_tokens=max(0, (u.prompt_tokens or 0) - cached),
                     completion_tokens=u.completion_tokens or 0, cache_read_tokens=cached)

    async def complete(self, req: ProviderRequest) -> Completion:
        try:
            resp = await self.client.chat.completions.create(**self._kwargs(req))
        except Exception as exc:
            raise map_openai_error(exc) from exc
        choice = resp.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            tool_calls = [ToolCall(id=tc.id, function=ToolCallFunction(name=tc.function.name,
                                                                        arguments=tc.function.arguments))
                          for tc in choice.message.tool_calls]
        return Completion(text=choice.message.content or "", model=resp.model,
                          finish_reason=choice.finish_reason or "stop", usage=self._usage(resp.usage),
                          tool_calls=tool_calls)

    async def stream(self, req: ProviderRequest) -> AsyncIterator[StreamEvent]:
        kw = self._kwargs(req) | {"stream": True, "stream_options": {"include_usage": True}}
        try:
            raw = await self.client.chat.completions.create(**kw)
            it = raw.__aiter__()
            first = await first_with_timeout(it, req.ttft_timeout_s)
        except Exception as exc:
            raise map_openai_error(exc) from exc

        tool_acc: dict[int, dict[str, str]] = {}

        def convert(chunk: Any) -> StreamEvent:
            ev = StreamEvent()
            if chunk.choices:
                c = chunk.choices[0]
                ev.delta = (c.delta.content or "") if c.delta else ""
                for tc in (c.delta.tool_calls or []) if c.delta else []:
                    slot = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] = tc.function.name or slot["name"]
                        slot["arguments"] += tc.function.arguments or ""
                ev.finish_reason = c.finish_reason
            if getattr(chunk, "usage", None):
                ev.usage = self._usage(chunk.usage)
            if ev.finish_reason and tool_acc:
                ev.tool_calls = [ToolCall(id=s["id"], function=ToolCallFunction(name=s["name"],
                                                                               arguments=s["arguments"] or "{}"))
                                 for s in tool_acc.values()]
            return ev

        yield convert(first)
        try:
            async for chunk in it:
                yield convert(chunk)
        except Exception as exc:  # mid-stream failures cannot fail over; surface them
            raise map_openai_error(exc) from exc

    async def aclose(self) -> None:
        await self.client.close()


def safe_json(s: str) -> Any:
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return {}
