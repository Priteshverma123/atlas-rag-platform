"""Provider adapter contract."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TypeVar

from atlas.gateway.schemas import Completion, ErrorKind, ProviderError, ProviderRequest, StreamEvent

T = TypeVar("T")


class Provider(ABC):
    """One configured upstream (e.g. 'anthropic', 'openai', 'ollama').

    Adapters must:
      * never retry internally (the router owns retries, so attempts are counted once);
      * map every failure to ``ProviderError`` with a precise ``ErrorKind``;
      * return usage in the gateway's non-overlapping buckets.
    """

    name: str

    @abstractmethod
    async def complete(self, req: ProviderRequest) -> Completion: ...

    @abstractmethod
    def stream(self, req: ProviderRequest) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None:  # pragma: no cover - optional
        return None


async def first_with_timeout(it: AsyncIterator[T], timeout_s: float) -> T:
    """Await the first item of a stream with a time-to-first-token deadline.

    Failing *before* the first token lets the router fail over transparently; once tokens have
    been sent to the client we can no longer switch providers.
    """
    try:
        return await asyncio.wait_for(anext(it), timeout=timeout_s)
    except TimeoutError as exc:
        raise ProviderError(ErrorKind.TIMEOUT, f"no first token within {timeout_s}s") from exc
