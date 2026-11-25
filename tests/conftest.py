from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fakeredis import aioredis

from atlas.gateway.cache.exact import ExactCache
from atlas.gateway.config import CooldownPolicy, GatewayConfig, ProviderConfig, RetryPolicy, RouteConfig, StrategyNode
from atlas.gateway.engine import CallContext, GatewayEngine
from atlas.gateway.limits.budgets import Budgets
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.gateway.pricing import Price, PriceTable
from atlas.gateway.providers.base import Provider
from atlas.gateway.resilience.health import TargetHealth
from atlas.gateway.spend import SpendRecorder


@pytest.fixture
async def redis() -> AsyncIterator[aioredis.FakeRedis]:
    r = aioredis.FakeRedis()
    yield r
    await r.aclose()


def make_engine(redis: aioredis.FakeRedis, providers: dict[str, Provider], strategy: StrategyNode,
                **route_kw: object) -> GatewayEngine:
    route = RouteConfig(strategy=strategy, retry_policy=RetryPolicy(base_delay_s=0.0, max_delay_s=0.0),
                        cooldown=CooldownPolicy(allowed_fails=2, window_s=60, cooldown_s=30), **route_kw)
    cfg = GatewayConfig(providers={n: ProviderConfig(type="mock") for n in providers}, routes={"r": route})
    import re

    pricing = PriceTable("test", [Price("p1", re.compile(".*"), 1.0, 2.0), Price("p2", re.compile(".*"), 1.0, 2.0)])
    return GatewayEngine(cfg, providers, TargetHealth(redis), RateLimiter(redis), Budgets(redis), pricing,
                         SpendRecorder(redis, None), ExactCache(redis), None)


@pytest.fixture
def ctx() -> CallContext:
    return CallContext(tenant_id="t1", key_id="k1")
