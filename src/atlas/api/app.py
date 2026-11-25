"""atlas-api: the public REST API (OpenAPI docs at /docs)."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from atlas.api.routers import admin, console, kb, memory_research, query
from atlas.common.errors import AtlasError
from atlas.common.logging import configure_logging, get_logger
from atlas.common.settings import get_settings
from atlas.common.telemetry import setup_telemetry
from atlas.container import Container
from atlas.db.session import get_engine
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.generation.pipeline import AnswerPipeline
from atlas.kb.service import KnowledgeBase
from atlas.memory.facts import FactStore
from atlas.retrieval.service import RetrievalService
from atlas.workers.queues import QUEUE_FOR

log = get_logger(__name__)
HTTP_REQS = Counter("atlas_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LAT = Histogram("atlas_http_latency_seconds", "HTTP latency", ["route"],
                     buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    setup_telemetry(f"{s.service_name}-api", s.otlp_endpoint, api_key=s.otlp_api_key, capture_content=s.capture_content, environment=s.env)
    c = Container.build(s)
    await c.objects.ensure_bucket()
    arq = await create_pool(RedisSettings.from_dsn(s.redis_url))

    async def enqueue(fn: str, *args: Any, **kw: Any) -> Any:
        kw.setdefault("_queue_name", QUEUE_FOR[fn])
        return await arq.enqueue_job(fn, *args, **kw)

    retrieval = RetrievalService(c)
    facts = FactStore(c.qdrant, c.embedder, c.llm)
    app.state.c = c
    app.state.arq = arq
    app.state.limiter = RateLimiter(c.redis)
    app.state.retrieval = retrieval
    app.state.facts = facts
    app.state.kb = KnowledgeBase(c, enqueue)
    app.state.pipeline = AnswerPipeline(c, retrieval, facts, enqueue)
    log.info("api_ready", env=s.env)
    yield
    await retrieval.aclose()
    await arq.aclose()
    await c.aclose()


app = FastAPI(title="Atlas", version="0.1.0", lifespan=lifespan,
              description="Multi-tenant RAG platform: hybrid retrieval, verified citations, memory, deep research.")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_methods=["*"],
                   allow_headers=["*"], allow_credentials=False)


@app.middleware("http")
async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")
    HTTP_REQS.labels(request.method, path, response.status_code).inc()
    HTTP_LAT.labels(path).observe(time.perf_counter() - t0)
    response.headers["x-request-id"] = rid
    return response


def _problem(status: int, code: str, detail: str, extra: dict[str, Any] | None = None,
             headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"type": f"https://atlas.dev/errors/{code}", "title": code, "status": status,
                         "detail": detail, **(extra or {})}, status_code=status,
                        media_type="application/problem+json", headers=headers)


@app.exception_handler(AtlasError)
async def atlas_error(_: Request, exc: AtlasError) -> JSONResponse:
    headers = {}
    if (ra := exc.details.get("retry_after")) is not None:
        headers["Retry-After"] = str(max(1, round(float(ra))))  # type: ignore[arg-type]
    return _problem(exc.status_code, exc.code, exc.message, {"details": exc.details} if exc.details else None, headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _problem(422, "invalid_input", "request validation failed", {"errors": exc.errors()[:20]})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error")
    return _problem(500, "internal_error", "internal server error")


@app.get("/health/live", tags=["health"])
async def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready(request: Request) -> JSONResponse:
    c: Container = request.app.state.c
    checks: dict[str, str] = {}
    for name, probe in (("postgres", _pg), ("redis", c.redis.ping), ("qdrant", c.qdrant.get_collections)):
        try:
            await probe()
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"error: {type(e).__name__}"
    ok = all(v == "ok" for v in checks.values())
    queues = {q: await c.redis.zcard(q) for q in set(QUEUE_FOR.values())}
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks, "queue_depth": queues},
                        status_code=200 if ok else 503)


async def _pg() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


for r in (admin.router, kb.router, query.router, memory_research.router, console.router):
    app.include_router(r)


def _instrument() -> None:
    """Server spans for every request + client spans (and W3C traceparent propagation) on every
    httpx call, so one trace spans api → gateway → provider."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    # exclude per-message ASGI receive/send spans: an SSE answer would otherwise emit one span per token
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/.*,metrics", exclude_spans=["receive", "send"])
    HTTPXClientInstrumentor().instrument()


_instrument()
