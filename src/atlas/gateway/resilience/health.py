"""Shared (Redis-backed) target health: cooldowns + latency EWMA.

State lives in Redis so every gateway replica sees the same cooldowns. A target "cools down"
after ``allowed_fails`` failures inside ``window_s``; it is skipped for ``cooldown_s`` and then
implicitly half-open (the next request probes it). This matches LiteLLM's semantics.
"""

from __future__ import annotations

import time

from redis.asyncio import Redis

from atlas.gateway.config import CooldownPolicy

_LAT_ALPHA = 0.2


class TargetHealth:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    async def is_cooling(self, target: str) -> bool:
        return bool(await self.r.exists(f"gw:cool:{target}"))

    async def cooling_set(self, targets: list[str]) -> set[str]:
        if not targets:
            return set()
        pipe = self.r.pipeline()
        for t in targets:
            pipe.exists(f"gw:cool:{t}")
        res = await pipe.execute()
        return {t for t, v in zip(targets, res, strict=True) if v}

    async def record_failure(self, target: str, policy: CooldownPolicy, *, immediate: bool = False) -> bool:
        """Returns True if the target entered cooldown."""
        key = f"gw:fail:{target}"
        fails = await self.r.incr(key)
        if int(fails) == 1:  # first failure opens the window
            await self.r.expire(key, policy.window_s)
        if immediate or int(fails) >= policy.allowed_fails:
            await self.r.set(f"gw:cool:{target}", str(time.time()), ex=policy.cooldown_s)
            await self.r.delete(key)
            return True
        return False

    async def record_success(self, target: str, latency_s: float) -> None:
        key = f"gw:lat:{target}"
        old = await self.r.get(key)
        new = latency_s if old is None else _LAT_ALPHA * latency_s + (1 - _LAT_ALPHA) * float(old)
        await self.r.set(key, f"{new:.4f}", ex=3600)

    async def latencies(self, targets: list[str]) -> dict[str, float]:
        if not targets:
            return {}
        vals = await self.r.mget([f"gw:lat:{t}" for t in targets])
        # Unknown targets sort first (0.0) so new deployments get explored.
        return {t: float(v) if v is not None else 0.0 for t, v in zip(targets, vals, strict=True)}
