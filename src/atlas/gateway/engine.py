"""The routing engine: strategy tree → ordered candidates → attempts with retries, cooldowns and
error-class-specific fallbacks; plus rate limits, budgets, caches and usage accounting.

    request ─▶ rate limit ─▶ budget ─▶ exact cache ─▶ semantic cache ─▶ candidates ─▶ attempt loop
                                                                                     │
                     spend ledger (async) ◀─ caches ◀─ cost ◀─ usage ◀───────────────┘
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import random
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

from atlas.common.errors import BudgetExceeded, InvalidInput, RateLimited, UpstreamError
from atlas.common.logging import get_logger
from atlas.common.telemetry import GenAI, span
from atlas.common.text import count_tokens, sha256_text
from atlas.gateway import metrics as m
from atlas.gateway.cache.exact import ExactCache, request_fingerprint
from atlas.gateway.cache.semantic import SemanticCache
from atlas.gateway.config import GatewayConfig, RouteConfig, StrategyNode
from atlas.gateway.limits.budgets import BudgetLimits, Budgets, BudgetState
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.gateway.pricing import PriceTable
from atlas.gateway.providers.base import Provider
from atlas.gateway.resilience.health import TargetHealth
from atlas.gateway.resilience.retry import backoff_delay
from atlas.gateway.schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Choice,
    Completion,
    ErrorKind,
    ProviderError,
    ProviderRequest,
    StreamEvent,
    Usage,
)
from atlas.gateway.spend import SpendRecorder
from pulse.sdk import _state as _pulse_state

log = get_logger(__name__)


@dataclass(slots=True)
class CallContext:
    tenant_id: str
    key_id: str
    rpm: int = 0
    tpm: int = 0
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    cache_tags: list[str] = field(default_factory=list)
    cache_bypass: bool = False
    cache_refresh: bool = False  # skip lookup but overwrite the entry (heals a poisoned/invalid cached value)


@dataclass(slots=True)
class Attempt:
    target: str
    outcome: str
    error: str | None = None
    latency_ms: float = 0.0


@dataclass(slots=True)
class Outcome:
    completion: Completion
    provider: str
    target: str
    attempts: list[Attempt]
    upstream_s: float


class GatewayEngine:
    def __init__(self, cfg: GatewayConfig, providers: dict[str, Provider], health: TargetHealth,
                 limiter: RateLimiter, budgets: Budgets, pricing: PriceTable, spend: SpendRecorder,
                 exact: ExactCache, semantic: SemanticCache | None) -> None:
        self.cfg = cfg
        self.providers = providers
        self.health = health
        self.limiter = limiter
        self.budgets = budgets
        self.pricing = pricing
        self.spend = spend
        self.exact = exact
        self.semantic = semantic

    # ------------------------------------------------------------------ planning
    async def order(self, node: StrategyNode) -> list[StrategyNode]:
        if node.target:
            return [node]
        children = list(node.targets)
        if node.mode == "loadbalance":
            # weighted shuffle (Efraimidis–Spirakis): sort by u^(1/w)
            children.sort(key=lambda c: -(random.random() ** (1.0 / max(c.weight, 1e-6))))
        elif node.mode == "latency":
            lats = await self.health.latencies([c.leaves()[0].target or "" for c in children])
            children.sort(key=lambda c: lats.get(c.leaves()[0].target or "", 0.0))
        out: list[StrategyNode] = []
        for c in children:
            out.extend(await self.order(c))
        return out

    def _resolve(self, req: ChatRequest) -> tuple[str, RouteConfig]:
        try:
            return self.cfg.route_for(req.model)
        except KeyError as exc:
            raise InvalidInput(f"unknown route or model '{req.model}'",
                               details={"routes": sorted(self.cfg.routes)}) from exc

    def _provider_request(self, req: ChatRequest, leaf: StrategyNode, route: RouteConfig) -> ProviderRequest:
        _, model = (leaf.target or "").split("/", 1)
        max_tokens = req.output_limit(route.default_max_tokens)
        if cap := leaf.params.get("max_tokens_cap"):
            max_tokens = min(max_tokens, int(cap))
        return ProviderRequest(
            model=model, messages=req.messages, max_tokens=max_tokens, temperature=req.temperature,
            tools=req.tools, tool_choice=req.tool_choice, response_format=req.response_format,
            timeout_s=route.timeouts.total_s, ttft_timeout_s=route.timeouts.ttft_s, cache_prompt=route.prompt_cache,
            extra={k: v for k, v in leaf.params.items() if k != "max_tokens_cap"})

    # ------------------------------------------------------------------ admission
    async def _admit(self, req: ChatRequest, ctx: CallContext) -> tuple[str, RouteConfig, int]:
        route_name, route = self._resolve(req)
        est = sum(count_tokens(msg.text()) for msg in req.messages) + req.output_limit(route.default_max_tokens)
        d = await self.limiter.check_request(ctx.key_id, ctx.rpm, ctx.tpm, est)
        if not d.allowed:
            raise RateLimited("rate limit exceeded", retry_after=d.retry_after_s)
        state = await self.budgets.check(ctx.tenant_id, ctx.key_id, ctx.limits)
        if state is BudgetState.HARD:
            raise BudgetExceeded("spend budget exhausted for this key or tenant")
        if state is BudgetState.SOFT and (down := route.downgrade_route) and down in self.cfg.routes:
            log.info("budget_soft_downgrade", route=route_name, to=down)
            route_name, route = down, self.cfg.routes[down]
        return route_name, route, est

    # ------------------------------------------------------------------ caching
    def _semantic_eligible(self, req: ChatRequest, route: RouteConfig) -> bool:
        return (self.semantic is not None and route.cache.semantic and not req.tools
                and len(req.messages) <= route.cache.semantic_max_messages and req.messages[-1].role == "user")

    def _semantic_ns(self, req: ChatRequest, route_name: str, tenant: str) -> str:
        system = "".join(msg.text() for msg in req.messages if msg.role in ("system", "developer"))
        return f"{tenant}:{route_name}:{sha256_text(system)[:16]}"

    async def _cache_lookup(self, req: ChatRequest, route_name: str, route: RouteConfig,
                            ctx: CallContext) -> ChatResponse | None:
        if ctx.cache_bypass or ctx.cache_refresh:
            return None
        if route.cache.exact:
            hit = await self.exact.get(ctx.tenant_id, request_fingerprint(req, route_name))
            if hit:
                hit.cache = "exact"
                return hit
        if self._semantic_eligible(req, route):
            assert self.semantic is not None
            try:
                found = await self.semantic.lookup(self._semantic_ns(req, route_name, ctx.tenant_id),
                                                   req.messages[-1].text(), route.cache.semantic_threshold,
                                                   route.cache.semantic_ttl_s)
            except Exception:  # cache must never take the request down
                log.warning("semantic_cache_lookup_failed", exc_info=True)
                found = None
            if found:
                value, score = found
                resp = ChatResponse.model_validate(value)
                resp.cache = f"semantic:{score:.3f}"
                return resp
        return None

    async def _cache_store(self, req: ChatRequest, route_name: str, route: RouteConfig, ctx: CallContext,
                           resp: ChatResponse) -> None:
        if ctx.cache_bypass or resp.choices[0].finish_reason not in ("stop", None):
            return
        try:
            if route.cache.exact:
                await self.exact.set(ctx.tenant_id, request_fingerprint(req, route_name), resp,
                                     route.cache.exact_ttl_s, ctx.cache_tags)
            if self._semantic_eligible(req, route):
                assert self.semantic is not None
                await self.semantic.store(self._semantic_ns(req, route_name, ctx.tenant_id), req.messages[-1].text(),
                                          resp.model_dump(), ctx.cache_tags)
        except Exception:
            log.warning("cache_store_failed", exc_info=True)

    # ------------------------------------------------------------------ execution
    async def _candidates(self, route: RouteConfig) -> deque[StrategyNode]:
        ordered = await self.order(route.strategy)
        cooling = await self.health.cooling_set([leaf.target or "" for leaf in ordered])
        healthy = [leaf for leaf in ordered if leaf.target not in cooling]
        # If everything is cooling down, probe anyway rather than fail fast (availability > purity).
        return deque(healthy or ordered)

    async def _attempt_loop(self, req: ChatRequest, route: RouteConfig, *, stream: bool
                            ) -> tuple[Outcome | None, AsyncIterator[StreamEvent] | None, StreamEvent | None]:
        pending = await self._candidates(route)
        tried: set[str] = set()
        attempts: list[Attempt] = []
        last: ProviderError | None = None

        def reroute(targets: list[str]) -> deque[StrategyNode]:
            return deque(StrategyNode(target=t) for t in targets if t not in tried)

        while pending:
            leaf = pending.popleft()
            target = leaf.target or ""
            if target in tried:
                continue
            tried.add(target)
            provider = self.providers.get(target.split("/", 1)[0])
            if provider is None:
                continue
            preq = self._provider_request(req, leaf, route)
            attempt_no = 0
            while True:
                attempt_no += 1
                t0 = time.perf_counter()
                try:
                    if stream:
                        agen = provider.stream(preq)
                        first = await anext(agen)  # adapters enforce the TTFT deadline
                        attempts.append(Attempt(target, "ok", latency_ms=(time.perf_counter() - t0) * 1000))
                        m.ATTEMPTS.labels(provider.name, "ok").inc()
                        await self.health.record_success(target, time.perf_counter() - t0)
                        return Outcome(Completion("", preq.model), provider.name, target, attempts,
                                       time.perf_counter() - t0), agen, first
                    comp = await provider.complete(preq)
                    if comp.finish_reason == "content_filter" and route.content_policy_fallbacks:
                        raise ProviderError(ErrorKind.CONTENT_POLICY, "refused by model")
                    elapsed = time.perf_counter() - t0
                    attempts.append(Attempt(target, "ok", latency_ms=elapsed * 1000))
                    m.ATTEMPTS.labels(provider.name, "ok").inc()
                    await self.health.record_success(target, elapsed)
                    return Outcome(comp, provider.name, target, attempts, elapsed), None, None
                except ProviderError as e:
                    last = e
                    attempts.append(Attempt(target, e.kind.value, str(e)[:300], (time.perf_counter() - t0) * 1000))
                    m.ATTEMPTS.labels(provider.name, e.kind.value).inc()
                    log.warning("upstream_error", target=target, kind=e.kind.value, attempt=attempt_no)
                    if e.kind is ErrorKind.BAD_REQUEST:
                        raise InvalidInput(f"upstream rejected the request: {e}") from e
                    if e.kind is ErrorKind.CONTEXT_WINDOW:
                        pending = reroute(route.context_window_fallbacks)
                        break
                    if e.kind is ErrorKind.CONTENT_POLICY:
                        pending = reroute(route.content_policy_fallbacks)
                        break
                    if e.kind is ErrorKind.AUTH:
                        await self.health.record_failure(target, route.cooldown, immediate=True)
                        m.COOLDOWNS.labels(target).inc()
                        break
                    if attempt_no <= route.retry_policy.attempts_for(e.kind.value):
                        delay = backoff_delay(attempt_no, route.retry_policy, e.retry_after)
                        if delay is not None:
                            await asyncio.sleep(delay)
                            continue
                    if await self.health.record_failure(target, route.cooldown):
                        m.COOLDOWNS.labels(target).inc()
                    break
        raise UpstreamError(f"all targets failed (last: {last})",
                            details={"attempts": [dataclasses.asdict(a) for a in attempts]})

    # ------------------------------------------------------------------ accounting
    async def _account(self, *, route_name: str, ctx: CallContext, provider: str | None, model: str | None,
                       usage: Usage, est: int, started: float, status: str, cache: str = "miss",
                       attempts: int = 1, error_kind: str | None = None, ttft_ms: float | None = None,
                       upstream_s: float = 0.0) -> float:
        cost = self.pricing.cost(provider or "", model or "", usage) if provider and cache == "miss" else 0.0
        uncached = self.pricing.uncached_cost(provider or "", model or "", usage) if provider else 0.0
        total_s = time.perf_counter() - started
        m.REQUESTS.labels(route_name, provider or "none", status, cache.split(":")[0]).inc()
        m.LATENCY.labels(route_name).observe(total_s)
        if upstream_s:
            m.OVERHEAD.labels(route_name).observe(max(0.0, total_s - upstream_s))
        if provider and cache == "miss":
            for bucket, n in (("input", usage.prompt_tokens), ("output", usage.completion_tokens),
                              ("cache_read", usage.cache_read_tokens), ("cache_write", usage.cache_write_tokens)):
                if n:
                    m.TOKENS.labels(provider, bucket).inc(n)
            m.COST.labels(provider).inc(cost)
            await self.budgets.charge(ctx.tenant_id, ctx.key_id, cost)
            await self.limiter.reconcile_tokens(ctx.key_id, ctx.tpm, est, usage.total_tokens)
        elif cache != "miss":
            m.SAVED.labels(cache.split(":")[0]).inc(uncached)
        sc = trace.get_current_span().get_span_context()
        await self.spend.record(
            tenant_id=ctx.tenant_id if _is_uuid(ctx.tenant_id) else None,
            api_key_id=ctx.key_id if _is_uuid(ctx.key_id) else None, route=route_name, provider=provider,
            model=model, prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens, cache_write_tokens=usage.cache_write_tokens, cost_usd=cost,
            uncached_cost_usd=uncached, latency_ms=total_s * 1000, ttft_ms=ttft_ms, status=status,
            error_kind=error_kind, cache=cache.split(":")[0], attempts=attempts,
            trace_id=format(sc.trace_id, "032x") if sc.is_valid else None)
        return cost

    # ------------------------------------------------------------------ public API
    async def chat(self, req: ChatRequest, ctx: CallContext) -> ChatResponse:
        started = time.perf_counter()
        route_name, route, est = await self._admit(req, ctx)
        with span(f"chat {route_name}", kind="generation", **{GenAI.OPERATION: "chat", "atlas.route": route_name,
                                                              "pulse.input": _prompt_preview(req)}) as s:
            cached = await self._cache_lookup(req, route_name, route, ctx)
            if cached:
                s.set_attribute("atlas.cache", cached.cache or "")
                s.set_attribute("pulse.output", _clip(cached.choices[0].message.text()))
                s.set_attribute("pulse.metadata.cache", cached.cache or "")
                await self._account(route_name=route_name, ctx=ctx, provider=cached.provider, model=cached.model,
                                    usage=cached.usage, est=est, started=started, status="ok",
                                    cache=cached.cache or "exact")
                return cached
            try:
                outcome, _, _ = await self._attempt_loop(req, route, stream=False)
            except UpstreamError:
                await self._account(route_name=route_name, ctx=ctx, provider=None, model=None, usage=Usage(),
                                    est=est, started=started, status="error", error_kind="all_failed")
                raise
            assert outcome is not None
            comp = outcome.completion
            s.set_attribute(GenAI.PROVIDER, outcome.provider)
            s.set_attribute(GenAI.RESPONSE_MODEL, comp.model)
            s.set_attribute(GenAI.INPUT_TOKENS, comp.usage.prompt_tokens)
            s.set_attribute(GenAI.OUTPUT_TOKENS, comp.usage.completion_tokens)
            s.set_attribute(GenAI.CACHE_READ_TOKENS, comp.usage.cache_read_tokens)
            s.set_attribute(GenAI.CACHE_WRITE_TOKENS, comp.usage.cache_write_tokens)
            # Our input_tokens already EXCLUDE cached tokens (disjoint buckets); tell Lumen so it
            # does not subtract cache reads a second time.
            s.set_attribute("lumen.usage.disjoint", True)
            cost = await self._account(route_name=route_name, ctx=ctx, provider=outcome.provider, model=comp.model,
                                       usage=comp.usage, est=est, started=started, status="ok",
                                       attempts=len(outcome.attempts), upstream_s=outcome.upstream_s)
            s.set_attribute("lumen.cost_usd", cost)  # authoritative gateway price; Lumen won't re-price
            s.set_attribute("pulse.cost_usd", cost)
            s.set_attribute("pulse.output", _clip(comp.text))
            s.set_attribute("pulse.metadata.attempts", json.dumps([a.target + ":" + a.outcome
                                                                    for a in outcome.attempts]))
            resp = ChatResponse(model=comp.model, provider=outcome.provider, cost_usd=round(cost, 8), cache="miss",
                                usage=comp.usage, choices=[Choice(
                                    message=ChatMessage(role="assistant", content=comp.text,
                                                        tool_calls=comp.tool_calls),
                                    finish_reason=comp.finish_reason)])
            await self._cache_store(req, route_name, route, ctx, resp)
            return resp

    async def chat_stream(self, req: ChatRequest, ctx: CallContext) -> AsyncIterator[str]:
        """Yields SSE ``data:`` lines in the OpenAI chunk format (traced as one generation)."""
        route_name, _ = self._resolve(req)
        with span(f"chat {route_name}", kind="generation", **{GenAI.OPERATION: "chat", "atlas.route": route_name,
                                                               "atlas.stream": True,
                                                               "pulse.input": _prompt_preview(req)}) as gs:
            async for line in self._chat_stream(req, ctx, gs):
                yield line

    async def _chat_stream(self, req: ChatRequest, ctx: CallContext, gs: Any) -> AsyncIterator[str]:
        started = time.perf_counter()
        route_name, route, est = await self._admit(req, ctx)
        cached = await self._cache_lookup(req, route_name, route, ctx)
        rid = f"chatcmpl-{random.getrandbits(64):016x}"

        def chunk(delta: str = "", finish: str | None = None, extra: dict[str, Any] | None = None) -> str:
            body: dict[str, Any] = {"id": rid, "object": "chat.completion.chunk", "created": int(time.time()),
                                    "model": req.model,
                                    "choices": [{"index": 0, "delta": {"content": delta} if delta else {},
                                                 "finish_reason": finish}]}
            if extra:
                body.update(extra)
            return f"data: {json.dumps(body)}\n\n"

        if cached:
            text = cached.choices[0].message.text()
            gs.set_attribute("pulse.output", _clip(text))
            gs.set_attribute("pulse.metadata.cache", cached.cache or "")
            for i in range(0, len(text), 64):
                yield chunk(text[i:i + 64])
            yield chunk(finish="stop", extra={"usage": cached.usage.model_dump(), "x_atlas": {"cache": cached.cache}})
            yield "data: [DONE]\n\n"
            await self._account(route_name=route_name, ctx=ctx, provider=cached.provider, model=cached.model,
                                usage=cached.usage, est=est, started=started, status="ok",
                                cache=cached.cache or "exact")
            return

        try:
            outcome, agen, first = await self._attempt_loop(req, route, stream=True)
        except UpstreamError as e:
            gs.set_attribute("pulse.level", "ERROR")
            gs.set_attribute("pulse.status_message", e.message[:500])
            yield f"data: {json.dumps({'error': {'message': e.message, 'type': 'upstream_error'}})}\n\n"
            await self._account(route_name=route_name, ctx=ctx, provider=None, model=None, usage=Usage(), est=est,
                                started=started, status="error", error_kind="all_failed")
            return
        assert outcome is not None and agen is not None and first is not None
        ttft_ms = (time.perf_counter() - started) * 1000
        m.TTFT.labels(route_name).observe(ttft_ms / 1000)
        parts: list[str] = []
        usage, finish, tool_calls = Usage(), "stop", None
        status, err_kind = "ok", None
        try:
            ev: StreamEvent | None = first
            while ev is not None:
                if ev.delta:
                    parts.append(ev.delta)
                    yield chunk(ev.delta)
                if ev.usage:
                    usage = ev.usage
                if ev.finish_reason:
                    finish = ev.finish_reason
                if ev.tool_calls:
                    tool_calls = ev.tool_calls
                ev = await anext(agen, None)
        except ProviderError as e:  # mid-stream: cannot fail over any more
            status, err_kind = "error", e.kind.value
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': e.kind.value}})}\n\n"
        target_model = outcome.target.split("/", 1)[1]
        cost = await self._account(route_name=route_name, ctx=ctx, provider=outcome.provider, model=target_model,
                                   usage=usage, est=est, started=started, status=status, error_kind=err_kind,
                                   attempts=len(outcome.attempts), ttft_ms=ttft_ms)
        gs.set_attribute(GenAI.PROVIDER, outcome.provider)
        gs.set_attribute(GenAI.RESPONSE_MODEL, target_model)
        gs.set_attribute(GenAI.INPUT_TOKENS, usage.prompt_tokens)
        gs.set_attribute(GenAI.OUTPUT_TOKENS, usage.completion_tokens)
        gs.set_attribute(GenAI.CACHE_READ_TOKENS, usage.cache_read_tokens)
        gs.set_attribute("pulse.cost_usd", cost)
        gs.set_attribute("pulse.output", _clip("".join(parts)))
        gs.set_attribute("pulse.metadata.ttft_ms", round(ttft_ms, 1))
        gs.set_attribute("pulse.metadata.attempts", json.dumps([a.target + ":" + a.outcome for a in outcome.attempts]))
        if status != "ok":
            gs.set_attribute("pulse.level", "ERROR")
            gs.set_attribute("pulse.status_message", f"stream failed: {err_kind}")
        if status == "ok":
            extra: dict[str, Any] = {"usage": usage.model_dump(),
                                     "x_atlas": {"provider": outcome.provider, "cost_usd": round(cost, 8)}}
            if tool_calls:
                extra["x_atlas"]["tool_calls"] = [tc.model_dump() for tc in tool_calls]
            yield chunk(finish=finish, extra=extra)
            yield "data: [DONE]\n\n"
            resp = ChatResponse(model=target_model, provider=outcome.provider, usage=usage, cost_usd=cost,
                                choices=[Choice(message=ChatMessage(role="assistant", content="".join(parts)),
                                                finish_reason=finish)])
            await self._cache_store(req, route_name, route, ctx, resp)


def _clip(text: str, n: int = 8000) -> str:
    if not _pulse_state.capture:
        return ""
    return text if len(text) <= n else text[:n] + "…[truncated]"


def _prompt_preview(req: ChatRequest) -> str:
    """Prompt as captured in the trace (Pulse SDK redaction/capture flags apply on the API side;
    the gateway clips size). System prompts are shortened: they're stable and huge."""
    if not _pulse_state.capture:
        return ""
    msgs = []
    for msg in req.messages:
        t = msg.text()
        if msg.role in ("system", "developer") and len(t) > 600:
            t = t[:600] + "…"
        msgs.append({"role": msg.role, "content": _clip(t, 6000)})
    return json.dumps(msgs, ensure_ascii=False)


def _is_uuid(v: str) -> bool:
    return len(v) == 36 and v.count("-") == 4
