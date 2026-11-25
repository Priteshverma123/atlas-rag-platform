"""Native Anthropic adapter (official ``anthropic`` SDK).

Translation notes (OpenAI dialect → Messages API):
  * system/developer messages → ``system`` blocks; the last block gets ``cache_control`` so the
    stable prefix (tools → system) is served from the provider prompt cache.
  * tool calls / tool results → ``tool_use`` / ``tool_result`` content blocks.
  * ``response_format: json_schema`` → ``output_config.format``.
  * sampling params are not sent (current Claude models reject them); depth is controlled with
    ``output_config.effort`` (route param ``effort``).
  * forced ``tool_choice`` (``required`` / named) is rejected by current models → sent as ``auto``.
  * ``stop_reason == "refusal"`` → ``finish_reason = "content_filter"`` so the router can use the
    route's content-policy fallback list. Server-side fallbacks can be enabled per provider.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from atlas.gateway.providers.base import Provider, first_with_timeout
from atlas.gateway.schemas import (
    ChatMessage,
    Completion,
    ErrorKind,
    ProviderError,
    ProviderRequest,
    StreamEvent,
    ToolCall,
    ToolCallFunction,
    Usage,
)

_STOP_MAP = {"end_turn": "stop", "max_tokens": "length", "stop_sequence": "stop", "tool_use": "tool_calls",
             "refusal": "content_filter", "pause_turn": "stop"}


def map_anthropic_error(exc: Exception) -> ProviderError:
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError(ErrorKind.TIMEOUT, str(exc))
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(ErrorKind.CONNECTION, str(exc))
    if isinstance(exc, anthropic.RateLimitError):
        ra = exc.response.headers.get("retry-after") if exc.response is not None else None
        return ProviderError(ErrorKind.RATE_LIMIT, str(exc), status=429, retry_after=float(ra) if ra else None)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return ProviderError(ErrorKind.AUTH, str(exc), status=exc.status_code)
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(exc).lower()
        if "prompt is too long" in msg or "context window" in msg or "too many tokens" in msg:
            return ProviderError(ErrorKind.CONTEXT_WINDOW, str(exc), status=400)
        return ProviderError(ErrorKind.BAD_REQUEST, str(exc), status=400)
    if isinstance(exc, anthropic.APIStatusError):
        kind = ErrorKind.OVERLOADED if exc.status_code in (502, 503, 504, 529) else ErrorKind.UNKNOWN
        return ProviderError(kind, str(exc), status=exc.status_code)
    return ProviderError(ErrorKind.UNKNOWN, repr(exc))


def to_anthropic(messages: list[ChatMessage], cache_prompt: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (system_blocks, messages) in Messages API shape."""
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    def push(role: str, blocks: list[dict[str, Any]]) -> None:
        # The Messages API requires alternating roles; merge consecutive same-role turns
        # (e.g. several tool results answering a parallel tool call).
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        if m.role in ("system", "developer"):
            if m.text():
                system.append({"type": "text", "text": m.text()})
        elif m.role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.text()}])
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.text():
                blocks.append({"type": "text", "text": m.text()})
            for tc in m.tool_calls or []:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.function.name, "input": args})
            if blocks:
                push("assistant", blocks)
        else:
            if isinstance(m.content, list):
                push("user", list(m.content))
            else:
                push("user", [{"type": "text", "text": m.text()}])
    if cache_prompt and system:
        system[-1]["cache_control"] = {"type": "ephemeral"}
    return system, out


def to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({"name": fn["name"], "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}})})
    return out


class AnthropicProvider(Provider):
    def __init__(self, name: str, api_key: str, *, server_fallbacks: bool = False) -> None:
        self.name = name
        self.server_fallbacks = server_fallbacks
        self.client = AsyncAnthropic(api_key=api_key, max_retries=0)

    def _kwargs(self, req: ProviderRequest) -> dict[str, Any]:
        system, messages = to_anthropic(req.messages, req.cache_prompt)
        kw: dict[str, Any] = {"model": req.model, "max_tokens": req.max_tokens, "messages": messages,
                              "timeout": req.timeout_s}
        if system:
            kw["system"] = system
        tools = to_anthropic_tools(req.tools)
        if tools:
            kw["tools"] = tools
            # Forced tool choice is rejected by current models: steer via prompt, keep 'auto'.
            kw["tool_choice"] = {"type": "none"} if req.tool_choice == "none" else {"type": "auto"}
        extra_body: dict[str, Any] = {}
        output_config: dict[str, Any] = {}
        if req.response_format and req.response_format.get("type") == "json_schema":
            schema = req.response_format.get("json_schema", {}).get("schema", {})
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if effort := req.extra.get("effort"):
            output_config["effort"] = effort
        if output_config:
            extra_body["output_config"] = output_config
        if self.server_fallbacks:
            extra_body["fallbacks"] = "default"
            kw["extra_headers"] = {"anthropic-beta": "server-side-fallback-2026-07-01"}
        if extra_body:
            kw["extra_body"] = extra_body
        return kw

    @staticmethod
    def _usage(u: Any) -> Usage:
        return Usage(prompt_tokens=getattr(u, "input_tokens", 0) or 0,
                     completion_tokens=getattr(u, "output_tokens", 0) or 0,
                     cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                     cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)

    @staticmethod
    def _parse(msg: Any) -> tuple[str, list[ToolCall] | None]:
        texts, calls = [], []
        for block in msg.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, function=ToolCallFunction(name=block.name,
                                                                              arguments=json.dumps(block.input))))
        return "".join(texts), calls or None

    async def complete(self, req: ProviderRequest) -> Completion:
        try:
            msg = await self.client.messages.create(**self._kwargs(req))
        except Exception as exc:
            raise map_anthropic_error(exc) from exc
        text, calls = self._parse(msg)
        return Completion(text=text, model=msg.model, finish_reason=_STOP_MAP.get(msg.stop_reason or "", "stop"),
                          usage=self._usage(msg.usage), tool_calls=calls)

    async def stream(self, req: ProviderRequest) -> AsyncIterator[StreamEvent]:
        kw = self._kwargs(req)
        try:
            async with self.client.messages.stream(**kw) as stream:
                texts = stream.text_stream.__aiter__()
                try:
                    first = await first_with_timeout(texts, req.ttft_timeout_s)
                    yield StreamEvent(delta=first)
                except StopAsyncIteration:
                    pass  # e.g. a pure tool-use turn: no text at all
                async for delta in texts:
                    yield StreamEvent(delta=delta)
                final = await stream.get_final_message()
                _, calls = self._parse(final)
                yield StreamEvent(finish_reason=_STOP_MAP.get(final.stop_reason or "", "stop"),
                                  usage=self._usage(final.usage), tool_calls=calls)
        except ProviderError:
            raise
        except Exception as exc:
            raise map_anthropic_error(exc) from exc

    async def aclose(self) -> None:
        await self.client.close()
