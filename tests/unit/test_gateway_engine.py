"""Routing engine behaviour: fallback, retries, cooldowns, error classes, caching, streaming."""

from __future__ import annotations

import pytest

from atlas.common.errors import InvalidInput, RateLimited, UpstreamError
from atlas.gateway.config import CachePolicy, StrategyNode
from atlas.gateway.engine import CallContext
from atlas.gateway.providers.mock import MockProvider
from atlas.gateway.schemas import ChatMessage, ChatRequest, ErrorKind
from tests.conftest import make_engine

FALLBACK = StrategyNode(mode="fallback", targets=[StrategyNode(target="p1/m"), StrategyNode(target="p2/m")])


def req(text: str = "hello", **kw: object) -> ChatRequest:
    return ChatRequest(model="r", messages=[ChatMessage(role="user", content=text)], **kw)  # type: ignore[arg-type]


async def test_happy_path_uses_primary(redis, ctx) -> None:
    p1, p2 = MockProvider("p1", reply=lambda r: "one"), MockProvider("p2", reply=lambda r: "two")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    resp = await eng.chat(req(), ctx)
    assert resp.choices[0].message.content == "one"
    assert resp.provider == "p1"
    assert p2.calls == 0


async def test_retry_then_success_on_same_target(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.OVERLOADED, fail_times=1, reply=lambda r: "ok")
    p2 = MockProvider("p2", reply=lambda r: "fallback")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    resp = await eng.chat(req(), ctx)
    assert resp.provider == "p1" and p1.calls == 2 and p2.calls == 0


async def test_fails_over_after_retries_exhausted(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.OVERLOADED, fail_times=-1)
    p2 = MockProvider("p2", reply=lambda r: "fallback")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    resp = await eng.chat(req(), ctx)
    assert resp.provider == "p2"
    assert p1.calls == 3  # 1 + 2 retries (overloaded=2)


async def test_cooldown_skips_failing_target_on_next_request(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.TIMEOUT, fail_times=-1)
    p2 = MockProvider("p2", reply=lambda r: "fallback")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    await eng.chat(req("a"), ctx)
    await eng.chat(req("b"), ctx)  # second failure → cooldown (allowed_fails=2)
    calls_before = p1.calls
    await eng.chat(req("c"), ctx)
    assert p1.calls == calls_before, "cooling target must be skipped"
    assert await eng.health.is_cooling("p1/m")


async def test_bad_request_fails_fast_without_fallback(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.BAD_REQUEST, fail_times=-1)
    p2 = MockProvider("p2")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    with pytest.raises(InvalidInput):
        await eng.chat(req(), ctx)
    assert p2.calls == 0


async def test_context_window_error_uses_context_fallback_list(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.CONTEXT_WINDOW, fail_times=-1)
    p2 = MockProvider("p2", reply=lambda r: "big-context model")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, StrategyNode(target="p1/m"),
                      context_window_fallbacks=["p2/m"])
    resp = await eng.chat(req(), ctx)
    assert resp.provider == "p2"


async def test_all_targets_fail_raises_upstream_error(redis, ctx) -> None:
    eng = make_engine(redis, {"p1": MockProvider("p1", fail_with=ErrorKind.OVERLOADED, fail_times=-1),
                              "p2": MockProvider("p2", fail_with=ErrorKind.CONNECTION, fail_times=-1)}, FALLBACK)
    with pytest.raises(UpstreamError) as ei:
        await eng.chat(req(), ctx)
    assert ei.value.details["attempts"]


async def test_exact_cache_hit_skips_provider(redis, ctx) -> None:
    p1 = MockProvider("p1", reply=lambda r: "cached answer")
    eng = make_engine(redis, {"p1": p1}, StrategyNode(target="p1/m"), cache=CachePolicy(exact=True))
    first = await eng.chat(req("same question"), ctx)
    second = await eng.chat(req("same question"), ctx)
    assert first.cache == "miss" and second.cache == "exact"
    assert p1.calls == 1
    other_tenant = await eng.chat(req("same question"), CallContext(tenant_id="t2", key_id="k2"))
    assert other_tenant.cache == "miss", "cache must be tenant-scoped"


async def test_rate_limit_rejects_with_retry_after(redis) -> None:
    eng = make_engine(redis, {"p1": MockProvider("p1")}, StrategyNode(target="p1/m"))
    ctx = CallContext(tenant_id="t", key_id="limited", rpm=1)
    await eng.chat(req("1"), ctx)
    with pytest.raises(RateLimited) as ei:
        await eng.chat(req("2"), ctx)
    assert ei.value.retry_after and ei.value.retry_after > 0


async def test_streaming_fails_over_before_first_token(redis, ctx) -> None:
    p1 = MockProvider("p1", fail_with=ErrorKind.TIMEOUT, fail_times=-1)
    p2 = MockProvider("p2", reply=lambda r: "streamed from p2")
    eng = make_engine(redis, {"p1": p1, "p2": p2}, FALLBACK)
    chunks = [c async for c in eng.chat_stream(req(stream=True), ctx)]
    body = "".join(chunks)
    assert "streamed" in body and "[DONE]" in body and '"provider": "p2"' in body


async def test_spend_is_recorded_asynchronously(redis, ctx) -> None:
    eng = make_engine(redis, {"p1": MockProvider("p1", reply=lambda r: "x" * 400)}, StrategyNode(target="p1/m"))
    await eng.chat(req("count me"), ctx)
    assert await redis.llen("gw:spend:queue") == 1
