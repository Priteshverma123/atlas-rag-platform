"""Backoff with full jitter (AWS architecture blog), honoring provider retry-after hints."""

from __future__ import annotations

import random

from atlas.gateway.config import RetryPolicy


def backoff_delay(attempt: int, policy: RetryPolicy, retry_after: float | None = None) -> float | None:
    """Seconds to wait before retry ``attempt`` (1-based), or None if we should fail over instead.

    A retry-after longer than ``max_retry_after_s`` means "this target is busy for a while": it's
    cheaper for the user to fail over to the next target than to sleep.
    """
    if retry_after is not None:
        return None if retry_after > policy.max_retry_after_s else retry_after
    cap = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt - 1)))
    return random.uniform(0, cap)
