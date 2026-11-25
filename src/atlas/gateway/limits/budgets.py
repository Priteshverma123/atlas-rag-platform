"""Hierarchical spend budgets: API key (daily) → tenant (monthly).

Checked *before* a call (fast Redis read) and charged *after* with the real cost. A soft limit
(default 90%) downgrades the request to the route's cheaper tier instead of failing; the hard
limit returns 402. Counters are approximate under heavy concurrency by design (one request may
overshoot by its own cost); the authoritative ledger is ``llm_calls`` in Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from redis.asyncio import Redis


class BudgetState(StrEnum):
    OK = "ok"
    SOFT = "soft"  # over soft threshold → downgrade
    HARD = "hard"  # over limit → reject


@dataclass(slots=True)
class BudgetLimits:
    key_daily_usd: float | None = None
    tenant_monthly_usd: float | None = None
    soft_ratio: float = 0.9


def _day() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _month() -> str:
    return datetime.now(UTC).strftime("%Y%m")


class Budgets:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    def _keys(self, tenant_id: str, key_id: str) -> tuple[str, str]:
        return f"spend:key:{key_id}:{_day()}", f"spend:tenant:{tenant_id}:{_month()}"

    async def check(self, tenant_id: str, key_id: str, limits: BudgetLimits) -> BudgetState:
        kday, tmon = self._keys(tenant_id, key_id)
        vals = await self.r.mget([kday, tmon])
        spent_key, spent_tenant = (float(v) if v else 0.0 for v in vals)
        state = BudgetState.OK
        for spent, limit in ((spent_key, limits.key_daily_usd), (spent_tenant, limits.tenant_monthly_usd)):
            if limit is None or limit <= 0:
                continue
            if spent >= limit:
                return BudgetState.HARD
            if spent >= limit * limits.soft_ratio:
                state = BudgetState.SOFT
        return state

    async def charge(self, tenant_id: str, key_id: str, cost_usd: float) -> None:
        if cost_usd <= 0:
            return
        kday, tmon = self._keys(tenant_id, key_id)
        pipe = self.r.pipeline()
        pipe.incrbyfloat(kday, cost_usd)
        pipe.expire(kday, 2 * 86400)
        pipe.incrbyfloat(tmon, cost_usd)
        pipe.expire(tmon, 40 * 86400)
        await pipe.execute()
