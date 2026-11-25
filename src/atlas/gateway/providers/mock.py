"""In-process deterministic provider for unit tests (failure injection + scripted outputs)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from atlas.gateway.providers.base import Provider
from atlas.gateway.schemas import (
    Completion,
    ErrorKind,
    ProviderError,
    ProviderRequest,
    StreamEvent,
    Usage,
)
from atlas.mockllm.logic import generate_reply


class MockProvider(Provider):
    def __init__(self, name: str = "mock", *, fail_with: ErrorKind | None = None, fail_times: int = 0,
                 latency_s: float = 0.0, reply: Callable[[ProviderRequest], str] | None = None) -> None:
        self.name = name
        self.fail_with = fail_with
        self.fail_times = fail_times  # fail the first N calls, then succeed (−1 = always fail)
        self.latency_s = latency_s
        self.reply = reply
        self.calls = 0

    def _maybe_fail(self) -> None:
        self.calls += 1
        if self.fail_with and (self.fail_times < 0 or self.calls <= self.fail_times):
            raise ProviderError(self.fail_with, f"injected {self.fail_with}",
                                retry_after=0.0 if self.fail_with == ErrorKind.RATE_LIMIT else None)

    def _text(self, req: ProviderRequest) -> str:
        if self.reply:
            return self.reply(req)
        return generate_reply([m.model_dump() for m in req.messages], req.response_format)

    async def complete(self, req: ProviderRequest) -> Completion:
        await asyncio.sleep(self.latency_s)
        self._maybe_fail()
        text = self._text(req)
        return Completion(text=text, model=req.model,
                          usage=Usage(prompt_tokens=sum(len(m.text()) // 4 for m in req.messages),
                                      completion_tokens=len(text) // 4))

    async def stream(self, req: ProviderRequest) -> AsyncIterator[StreamEvent]:
        c = await self.complete(req)
        for i in range(0, len(c.text), 16):
            yield StreamEvent(delta=c.text[i:i + 16])
        yield StreamEvent(finish_reason="stop", usage=c.usage)
