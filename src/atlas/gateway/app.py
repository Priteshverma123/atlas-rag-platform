"""atlas-gateway: OpenAI-compatible multi-provider LLM gateway.

    POST /v1/chat/completions     route name ("answer", "fast", ...) or explicit "provider/model"
    GET  /v1/models               routes and their resolved targets
    POST /admin/cache/invalidate  {"tenant_id": "...", "tags": ["doc:<id>"]}
    GET  /health/live | /health/ready | /metrics

Auth: ``Authorization: Bearer <atlas api key>`` or the internal service key plus
``x-atlas-tenant`` / ``x-atlas-key-id`` headers (trusted east-west traffic from atlas-api).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from atlas.common.errors import AtlasError, Forbidden, Unauthorized
from atlas.common.logging import configure_logging, get_logger
from atlas.common.settings import get_settings
from atlas.common.telemetry import setup_telemetry
from atlas.db.apikeys import constant_time_equals, lookup_key
from atlas.db.session import get_engine
from atlas.embeddings.client import Embedder
from atlas.gateway.cache.exact import ExactCache
from atlas.gateway.cache.semantic import SemanticCache
from atlas.gateway.config import load_config
from atlas.gateway.engine import CallContext, GatewayEngine
from atlas.gateway.limits.budgets import BudgetLimits, Budgets
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.gateway.pricing import PriceTable
from atlas.gateway.providers.registry import build_providers
from atlas.gateway.resilience.health import TargetHealth
from atlas.gateway.schemas import ChatRequest
from atlas.gateway.spend import SpendRecorder

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    setup_telemetry(f"{s.service_name}-gateway", s.otlp_endpoint, api_key=s.otlp_api_key, capture_content=s.capture_content, environment=s.env)
    cfg = load_config(s.config_path(s.gateway_config_file))
    providers = build_providers(cfg)
    redis = Redis.from_url(s.redis_url, decode_responses=False)
    qdrant = AsyncQdrantClient(url=s.qdrant_url,
                               api_key=s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None)
    embedder = Embedder(s, redis)
    spend = SpendRecorder(redis, get_engine())
    spend.start()
    app.state.redis = redis
    app.state.engine = GatewayEngine(
        cfg, providers, TargetHealth(redis), RateLimiter(redis), Budgets(redis),
        PriceTable.load(s.config_path(s.pricing_file)), spend, ExactCache(redis), SemanticCache(qdrant, embedder))
    log.info("gateway_ready", providers=sorted(providers), routes=sorted(cfg.routes))
    yield
    await spend.stop()
    for p in providers.values():
        await p.aclose()
    await embedder.aclose()
    await qdrant.close()
    await redis.aclose()


app = FastAPI(title="atlas-gateway", version="0.1.0", lifespan=lifespan)


@app.exception_handler(AtlasError)
async def atlas_error(_: Request, exc: AtlasError) -> JSONResponse:
    headers = {}
    if retry := exc.details.get("retry_after"):
        headers["Retry-After"] = str(max(1, round(float(retry))))  # type: ignore[arg-type]
    return JSONResponse({"error": {"message": exc.message, "type": exc.code, "details": exc.details}},
                        status_code=exc.status_code, headers=headers)


async def call_context(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_atlas_tenant: Annotated[str | None, Header()] = None,
    x_atlas_key_id: Annotated[str | None, Header()] = None,
    x_atlas_cache_tags: Annotated[str | None, Header()] = None,
    x_atlas_cache: Annotated[str | None, Header()] = None,
) -> CallContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    token = authorization[7:].strip()
    tags = [t for t in (x_atlas_cache_tags or "").split(",") if t]
    mode = (x_atlas_cache or "").lower()
    bypass, refresh = mode == "bypass", mode == "refresh"
    s = get_settings()
    if constant_time_equals(token, s.gateway_internal_key.get_secret_value()):
        return CallContext(tenant_id=x_atlas_tenant or "internal", key_id=x_atlas_key_id or "internal",
                           cache_tags=tags, cache_bypass=bypass, cache_refresh=refresh)
    info = await lookup_key(request.app.state.redis, token)
    if info is None:
        raise Unauthorized("invalid api key")
    if not info.has("llm"):
        raise Forbidden("key lacks the 'llm' scope")
    return CallContext(tenant_id=info.tenant_id, key_id=info.key_id, rpm=info.rpm, tpm=info.tpm,
                       limits=BudgetLimits(info.budget_usd_day, info.tenant_budget_month),
                       cache_tags=tags, cache_bypass=bypass, cache_refresh=refresh)


Ctx = Annotated[CallContext, Depends(call_context)]


@app.post("/v1/chat/completions", response_model=None)
async def chat_completions(req: ChatRequest, ctx: Ctx, request: Request) -> Any:
    engine: GatewayEngine = request.app.state.engine
    if req.stream:
        return StreamingResponse(engine.chat_stream(req, ctx), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    resp = await engine.chat(req, ctx)
    return JSONResponse(resp.model_dump(exclude_none=True),
                        headers={"x-atlas-provider": resp.provider or "", "x-atlas-cache": resp.cache or "miss",
                                 "x-atlas-cost-usd": f"{resp.cost_usd or 0:.8f}"})


@app.get("/v1/models")
async def models(request: Request, _: Ctx) -> dict[str, Any]:
    engine: GatewayEngine = request.app.state.engine
    return {"object": "list", "data": [
        {"id": name, "object": "model", "targets": [leaf.target for leaf in r.strategy.leaves()]}
        for name, r in engine.cfg.routes.items()]}


class Invalidate(BaseModel):
    tenant_id: str
    tags: list[str]


@app.post("/admin/cache/invalidate")
async def invalidate(body: Invalidate, request: Request, ctx: Ctx) -> dict[str, int]:
    if ctx.key_id != "internal":
        raise Forbidden("internal key required")
    engine: GatewayEngine = request.app.state.engine
    n = await engine.exact.invalidate_tags(body.tenant_id, body.tags)
    semantic_ok = 1
    if engine.semantic:
        try:
            await engine.semantic.invalidate_tags(body.tags)
        except Exception:
            log.warning("semantic_invalidation_failed", exc_info=True)
            semantic_ok = 0
    return {"exact_deleted": n, "semantic_ok": semantic_ok}


@app.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    try:
        await request.app.state.redis.ping()
    except Exception as exc:
        return JSONResponse({"status": "degraded", "redis": str(exc)}, status_code=503)
    return JSONResponse({"status": "ok", "providers": sorted(request.app.state.engine.providers)})


@app.get("/metrics")
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _instrument() -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    # exclude per-message ASGI receive/send spans: an SSE answer would otherwise emit one span per token
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics", exclude_spans=["receive", "send"])


_instrument()
