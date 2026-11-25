from __future__ import annotations

import re
from pathlib import Path

import pytest

from atlas.gateway.config import GatewayConfig, RetryPolicy, StrategyNode, load_config
from atlas.gateway.limits.budgets import BudgetLimits, Budgets, BudgetState
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.gateway.pricing import Price, PriceTable, Tier
from atlas.gateway.providers.anthropic import to_anthropic
from atlas.gateway.resilience.retry import backoff_delay
from atlas.gateway.schemas import ChatMessage, ToolCall, ToolCallFunction, Usage

ROOT = Path(__file__).resolve().parents[2]


def test_pricing_no_double_counting_and_tiers() -> None:
    t = PriceTable("v", [Price("anthropic", re.compile("claude-haiku.*"), 0.10, 0.50,
                                tiers=[Tier(100_000, 0.50, 2.50)]),
                          Price("anthropic", re.compile("claude-.*"), 9.0, 9.0)])
    small = Usage(prompt_tokens=1000, completion_tokens=1000)
    assert t.cost("anthropic", "claude-haiku-5-5", small) == pytest.approx((1000 * 0.10 + 1000 * 0.50) / 1e6)
    # most specific pattern wins over the generic claude-.* entry
    assert t.find("anthropic", "claude-haiku-5-5").input == 0.10  # type: ignore[union-attr]
    assert t.cost("unknown", "x", small) == 0.0


def test_long_context_tier_applies() -> None:
    t = PriceTable("v", [Price("a", re.compile("m"), 0.10, 0.50, tiers=[Tier(100_000, 0.50, 2.50)])])
    u = Usage(prompt_tokens=150_000, completion_tokens=0)
    assert t.cost("a", "m", u) == pytest.approx(150_000 * 0.50 / 1e6)


def test_cache_read_tokens_priced_separately() -> None:
    t = PriceTable("v", [Price("a", re.compile("m"), 2.0, 10.0, cache_read=0.10)])
    u = Usage(prompt_tokens=1000, cache_read_tokens=9000, completion_tokens=100)
    assert t.cost("a", "m", u) == pytest.approx((1000 * 2.0 + 9000 * 0.10 + 100 * 10.0) / 1e6)
    assert t.uncached_cost("a", "m", u) > t.cost("a", "m", u)


def test_backoff_honors_short_retry_after_and_fails_over_on_long() -> None:
    p = RetryPolicy(max_retry_after_s=5)
    assert backoff_delay(1, p, retry_after=2) == 2
    assert backoff_delay(1, p, retry_after=60) is None
    assert 0 <= backoff_delay(3, p) <= p.base_delay_s * 4  # type: ignore[operator]


def test_strategy_node_validation() -> None:
    with pytest.raises(ValueError):
        StrategyNode()
    with pytest.raises(ValueError):
        StrategyNode(mode="fallback", targets=[])


def test_config_prunes_providers_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in ("ATLAS_ANTHROPIC_API_KEY", "ATLAS_OPENAI_API_KEY", "ATLAS_GEMINI_API_KEY", "ATLAS_XAI_API_KEY",
              "ATLAS_OLLAMA_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config(ROOT / "config" / "gateway.yaml")
    for r in cfg.routes.values():
        assert [leaf.target for leaf in r.strategy.leaves()] == ["mock/atlas-mock"]
    monkeypatch.setenv("ATLAS_ANTHROPIC_API_KEY", "sk-test")
    cfg = load_config(ROOT / "config" / "gateway.yaml")
    assert cfg.routes["answer"].strategy.leaves()[0].target == "mock/atlas-mock", "keys alone must not go live"
    monkeypatch.setenv("ATLAS_LLM_LIVE", "true")
    cfg = load_config(ROOT / "config" / "gateway.yaml")
    assert cfg.routes["answer"].strategy.leaves()[0].target == "anthropic/claude-sonnet-5-5"


def test_explicit_provider_model_route() -> None:
    cfg = GatewayConfig.model_validate({"providers": {"mock": {"type": "mock"}},
                                        "routes": {"r": {"strategy": {"target": "mock/x"}}}})
    name, route = cfg.route_for("mock/other")
    assert name == "direct:mock/other" and route.strategy.target == "mock/other"


async def test_token_bucket(redis) -> None:
    rl = RateLimiter(redis)
    assert (await rl.take("k", per_minute=2)).allowed
    assert (await rl.take("k", per_minute=2)).allowed
    d = await rl.take("k", per_minute=2)
    assert not d.allowed and d.retry_after_s > 0


async def test_budgets_soft_and_hard(redis) -> None:
    b = Budgets(redis)
    lim = BudgetLimits(key_daily_usd=1.0)
    assert await b.check("t", "k", lim) is BudgetState.OK
    await b.charge("t", "k", 0.95)
    assert await b.check("t", "k", lim) is BudgetState.SOFT
    await b.charge("t", "k", 0.10)
    assert await b.check("t", "k", lim) is BudgetState.HARD


def test_anthropic_translation_merges_tool_results_and_caches_system() -> None:
    msgs = [ChatMessage(role="system", content="rules"), ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content=None, tool_calls=[
                ToolCall(id="t1", function=ToolCallFunction(name="a", arguments='{"x":1}')),
                ToolCall(id="t2", function=ToolCallFunction(name="b", arguments="{}"))]),
            ChatMessage(role="tool", tool_call_id="t1", content="r1"),
            ChatMessage(role="tool", tool_call_id="t2", content="r2")]
    system, out = to_anthropic(msgs, cache_prompt=True)
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result"]
    assert out[1]["content"][0]["input"] == {"x": 1}
