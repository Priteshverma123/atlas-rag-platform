"""Atomic token-bucket rate limiting in Redis (one Lua script = one round trip, race-free).

Two buckets per API key: requests/min and tokens/min. TPM is *estimated* before the call
(prompt tokens + max output) and *reconciled* afterwards with the provider's real usage, so
callers that ask for huge ``max_tokens`` but use few are not over-charged for long.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

# KEYS[1]=bucket  ARGV: capacity, refill_per_sec, now, cost, force(0/1)
# Returns {allowed(0/1), retry_after_ms, tokens_left}
_LUA = """
local cap = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local force = tonumber(ARGV[5])
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or cap
local ts = tonumber(b[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local allowed = 0
local retry = 0
if tokens >= cost or force == 1 then
  tokens = tokens - cost
  allowed = 1
else
  retry = math.ceil((cost - tokens) / rate * 1000)
end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate * 1000) + 1000)
return {allowed, retry, math.floor(tokens)}
"""


@dataclass(slots=True)
class Decision:
    allowed: bool
    retry_after_s: float
    remaining: int


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.r = redis
        self._script = redis.register_script(_LUA)

    async def take(self, key: str, per_minute: int, cost: float = 1.0, *, force: bool = False) -> Decision:
        if per_minute <= 0:  # 0 = unlimited
            return Decision(True, 0.0, -1)
        res = await self._script(keys=[f"rl:{key}"],
                                 args=[per_minute, per_minute / 60.0, time.time(), cost, int(force)])
        allowed, retry_ms, left = (int(x) for x in res)
        return Decision(bool(allowed), retry_ms / 1000.0, left)

    async def check_request(self, key_id: str, rpm: int, tpm: int, est_tokens: int) -> Decision:
        d = await self.take(f"{key_id}:rpm", rpm, 1)
        if not d.allowed:
            return d
        return await self.take(f"{key_id}:tpm", tpm, est_tokens)

    async def reconcile_tokens(self, key_id: str, tpm: int, estimated: int, actual: int) -> None:
        delta = actual - estimated
        if tpm > 0 and delta:
            # Positive delta debits more (force, may go negative); negative delta refunds.
            await self.take(f"{key_id}:tpm", tpm, delta, force=True)
