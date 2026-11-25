"""Pulse server: OTLP ingest + query API for the tracing console.

    POST /v1/traces                      OTLP/HTTP (protobuf or JSON, optional gzip)
    GET  /api/traces | /api/traces/{id}  list (filters, search, paging) / tree + scores
    GET  /api/observations               all observations + facet counts (filter sidebar)
    GET  /api/sessions | /api/sessions/{id}
    GET  /api/users
    GET  /api/metrics/overview           home dashboard
    GET|POST /api/scores                 evaluation + human annotation scores
Auth: ``Authorization: Bearer <project key>`` (keys configured via PULSE_PROJECTS="name:key,...").
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from pulse.server import db
from pulse.server.store import QUEUE, Ingestor

DB_URL = os.environ.get("PULSE_DATABASE_URL", "postgresql+asyncpg://atlas:atlas@localhost:5432/pulse")
REDIS_URL = os.environ.get("PULSE_REDIS_URL")
PROJECTS = os.environ.get("PULSE_PROJECTS", "default:pk-pulse-dev")
RETENTION_DAYS = int(os.environ.get("PULSE_RETENTION_DAYS", "30"))


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def _ensure_database() -> None:
    """Create the Pulse database on first boot (works on an existing Postgres volume too)."""
    import asyncpg

    url = DB_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(url)
        await conn.close()
    except asyncpg.InvalidCatalogNameError:
        base, dbname = url.rsplit("/", 1)
        admin = await asyncpg.connect(f"{base}/postgres")
        try:
            await admin.execute(f'CREATE DATABASE "{dbname.split("?")[0]}"')
        except asyncpg.DuplicateDatabaseError:
            pass
        finally:
            await admin.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await _ensure_database()
    engine: AsyncEngine = create_async_engine(DB_URL, pool_size=10, pool_pre_ping=True)
    async with engine.begin() as conn:
        # several server workers boot at once: serialise DDL with an advisory lock
        await conn.execute(text("SELECT pg_advisory_xact_lock(727274)"))
        await conn.run_sync(db.metadata.create_all)
        for item in filter(None, (p.strip() for p in PROJECTS.split(","))):
            name, key = item.split(":", 1)
            await conn.execute(insert(db.projects).values(id=name, key_hash=_hash(key)).on_conflict_do_update(
                index_elements=["id"], set_={"key_hash": _hash(key)}))
    redis = Redis.from_url(REDIS_URL) if REDIS_URL else None
    ing = Ingestor(engine, redis)
    ing.start()

    async def retention_loop() -> None:
        while True:
            await asyncio.sleep(3600)
            await ing.retention(RETENTION_DAYS)

    ret = asyncio.create_task(retention_loop())
    app.state.engine, app.state.redis, app.state.ingestor = engine, redis, ing
    app.state.keys = {}
    yield
    ret.cancel()
    await ing.stop()
    if redis:
        await redis.aclose()
    await engine.dispose()


app = FastAPI(title="Pulse", version="0.1.0", lifespan=lifespan,
              description="Self-hosted LLM tracing & observability")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("PULSE_CORS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


async def project(request: Request, authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer project key")
    h = _hash(authorization[7:].strip())
    cache: dict[str, str] = request.app.state.keys
    if h not in cache:
        async with request.app.state.engine.connect() as conn:
            row = (await conn.execute(text("SELECT id FROM pulse_projects WHERE key_hash = :h"), {"h": h})).first()
        if row is None:
            raise HTTPException(401, "invalid project key")
        cache[h] = row[0]
    return cache[h]


Project = Annotated[str, Depends(project)]


async def q(request: Request, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with request.app.state.engine.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(sql), params)).mappings()]


def _window(from_ts: datetime | None, to_ts: datetime | None, default_hours: int = 24) -> tuple[datetime, datetime]:
    to_ts = to_ts or datetime.now(UTC) + timedelta(minutes=1)
    return from_ts or to_ts - timedelta(hours=default_hours), to_ts


# ------------------------------------------------------------------------------------------- ingest
@app.post("/v1/traces")
async def otlp_traces(request: Request, project_id: Project) -> Response:
    body = await request.body()
    ok = await request.app.state.ingestor.accept(project_id, body, request.headers.get("content-type", ""),
                                                 request.headers.get("content-encoding", ""))
    if not ok:
        return Response(status_code=429, headers={"Retry-After": "5"})
    ct = "application/json" if "json" in request.headers.get("content-type", "") else "application/x-protobuf"
    return Response(content=b"{}" if ct == "application/json" else b"", media_type=ct)


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    depth = await request.app.state.redis.llen(QUEUE) if request.app.state.redis else 0
    return {"status": "ok", "queue_depth": depth, **request.app.state.ingestor.stats}


# ------------------------------------------------------------------------------------------- traces
@app.get("/api/traces")
async def list_traces(request: Request, project_id: Project, from_ts: datetime | None = None,
                      to_ts: datetime | None = None, name: str | None = None, user_id: str | None = None,
                      session_id: str | None = None, level: str | None = None, tag: str | None = None,
                      search: str | None = None, limit: int = Query(50, le=500), offset: int = 0) -> dict[str, Any]:
    f, t = _window(from_ts, to_ts, 24 * 7)
    where = ["project_id = :p", "start_time BETWEEN :f AND :t"]
    params: dict[str, Any] = {"p": project_id, "f": f, "t": t, "lim": limit, "off": offset}
    for col, val in (("name", name), ("user_id", user_id), ("session_id", session_id), ("level", level)):
        if val:
            where.append(f"{col} = :{col}")
            params[col] = val
    if tag:
        where.append(":tag = ANY(tags)")
        params["tag"] = tag
    if search:
        where.append("(name ILIKE :s OR input ILIKE :s OR output ILIKE :s OR trace_id = :raw)")
        params["s"], params["raw"] = f"%{search}%", search
    w = " AND ".join(where)
    rows = await q(request, f"""SELECT trace_id, name, user_id, session_id, tags, left(input, 300) AS input,
        left(output, 300) AS output, start_time, latency_ms, total_cost, input_tokens, output_tokens,
        observation_count, level, environment, services
        FROM pulse_traces WHERE {w} ORDER BY start_time DESC LIMIT :lim OFFSET :off""", **params)
    total = (await q(request, f"SELECT count(*) AS n FROM pulse_traces WHERE {w}", **params))[0]["n"]
    names = await q(request, f"SELECT name, count(*) AS n FROM pulse_traces WHERE {w} GROUP BY name ORDER BY n DESC "
                             "LIMIT 50", **params)
    return {"total": total, "data": rows, "facets": {"name": names}}


def _visible_tree(obs: list[dict[str, Any]], include_internal: bool) -> list[dict[str, Any]]:
    """Hide transport-level (http) spans but re-parent their children to the nearest visible ancestor."""
    if include_internal:
        return obs
    by_id = {o["span_id"]: o for o in obs}
    hidden = {o["span_id"] for o in obs if o["type"] == "http"}
    out = []
    for o in obs:
        if o["span_id"] in hidden:
            continue
        p = o["parent_span_id"]
        while p in hidden:
            p = by_id[p]["parent_span_id"]
        out.append({**o, "parent_span_id": p if p in by_id else None})
    return out


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str, request: Request, project_id: Project, include_internal: bool = False
                    ) -> dict[str, Any]:
    tr = await q(request, "SELECT * FROM pulse_traces WHERE project_id = :p AND trace_id = :t", p=project_id, t=trace_id)
    if not tr:
        raise HTTPException(404, "trace not found")
    obs = await q(request, """SELECT span_id, parent_span_id, name, type, start_time, end_time, latency_ms, level,
        status_message, model, provider, input_tokens, output_tokens, cache_read_tokens, total_tokens, cost, input,
        output, metadata, service FROM pulse_observations WHERE project_id = :p AND trace_id = :t ORDER BY start_time""",
                  p=project_id, t=trace_id)
    sc = await q(request, "SELECT * FROM pulse_scores WHERE project_id = :p AND trace_id = :t ORDER BY created_at",
                 p=project_id, t=trace_id)
    return {"trace": tr[0], "observations": _visible_tree(obs, include_internal), "scores": sc}


@app.get("/api/observations")
async def list_observations(request: Request, project_id: Project, from_ts: datetime | None = None,
                            to_ts: datetime | None = None, type: str | None = None, name: str | None = None,
                            level: str | None = None, root_only: bool | None = None, search: str | None = None,
                            limit: int = Query(50, le=500), offset: int = 0) -> dict[str, Any]:
    f, t = _window(from_ts, to_ts, 24 * 7)
    where = ["o.project_id = :p", "o.start_time BETWEEN :f AND :t", "o.type <> 'http'"]
    params: dict[str, Any] = {"p": project_id, "f": f, "t": t, "lim": limit, "off": offset}
    for col, val in (("type", type), ("name", name), ("level", level)):
        if val:
            where.append(f"o.{col} = ANY(:{col})")
            params[col] = val.split(",")
    if root_only is True:
        where.append("(o.parent_span_id IS NULL OR o.trace_name = o.name)")
    if search:
        where.append("(o.name ILIKE :s OR o.input ILIKE :s OR o.output ILIKE :s)")
        params["s"] = f"%{search}%"
    w = " AND ".join(where)
    rows = await q(request, f"""SELECT o.trace_id, o.span_id, o.name, o.type, o.trace_name, o.start_time, o.latency_ms,
        o.level, o.model, o.total_tokens, o.cost, left(o.input, 300) AS input, left(o.output, 300) AS output,
        o.metadata, o.user_id, o.service FROM pulse_observations o WHERE {w}
        ORDER BY o.start_time DESC LIMIT :lim OFFSET :off""", **params)
    total = (await q(request, f"SELECT count(*) AS n FROM pulse_observations o WHERE {w}", **params))[0]["n"]
    base = {"p": project_id, "f": f, "t": t}
    bw = "project_id = :p AND start_time BETWEEN :f AND :t AND type <> 'http'"
    facets = {k: await q(request, f"SELECT {k} AS value, count(*) AS n FROM pulse_observations WHERE {bw} "
                                  f"GROUP BY {k} ORDER BY n DESC LIMIT 50", **base) for k in ("name", "type", "level")}
    return {"total": total, "data": rows, "facets": facets}


# ------------------------------------------------------------------------------------------- sessions & users
@app.get("/api/sessions")
async def sessions(request: Request, project_id: Project, from_ts: datetime | None = None, to_ts: datetime | None = None,
                   limit: int = Query(50, le=500), offset: int = 0) -> dict[str, Any]:
    f, t = _window(from_ts, to_ts, 24 * 30)
    rows = await q(request, """SELECT session_id, count(*) AS traces, min(start_time) AS first_seen,
        max(start_time) AS last_seen, array_agg(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS users,
        sum(total_cost) AS cost, sum(input_tokens + output_tokens) AS tokens, sum(latency_ms) AS total_latency_ms
        FROM pulse_traces WHERE project_id = :p AND session_id IS NOT NULL AND start_time BETWEEN :f AND :t
        GROUP BY session_id ORDER BY last_seen DESC LIMIT :lim OFFSET :off""",
                   p=project_id, f=f, t=t, lim=limit, off=offset)
    return {"data": rows}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str, request: Request, project_id: Project) -> dict[str, Any]:
    rows = await q(request, """SELECT trace_id, name, user_id, input, output, start_time, latency_ms, total_cost,
        input_tokens, output_tokens, level FROM pulse_traces WHERE project_id = :p AND session_id = :s
        ORDER BY start_time""", p=project_id, s=session_id)
    return {"session_id": session_id, "traces": rows}


@app.get("/api/users")
async def users(request: Request, project_id: Project, from_ts: datetime | None = None,
                to_ts: datetime | None = None) -> dict[str, Any]:
    f, t = _window(from_ts, to_ts, 24 * 30)
    rows = await q(request, """SELECT user_id, count(*) AS traces, count(DISTINCT session_id) AS sessions,
        min(start_time) AS first_seen, max(start_time) AS last_seen, sum(total_cost) AS cost,
        sum(input_tokens + output_tokens) AS tokens,
        count(*) FILTER (WHERE level = 'ERROR') AS errors
        FROM pulse_traces WHERE project_id = :p AND user_id IS NOT NULL AND start_time BETWEEN :f AND :t
        GROUP BY user_id ORDER BY last_seen DESC LIMIT 500""", p=project_id, f=f, t=t)
    return {"data": rows}


# ------------------------------------------------------------------------------------------- dashboard
def _bucket(f: datetime, t: datetime) -> timedelta:
    """asyncpg binds Postgres intervals from timedelta (a string like '1 hour' is rejected)."""
    span = t - f
    if span <= timedelta(hours=6):
        return timedelta(minutes=5)
    if span <= timedelta(days=2):
        return timedelta(hours=1)
    return timedelta(days=1)


@app.get("/api/metrics/overview")
async def overview(request: Request, project_id: Project, from_ts: datetime | None = None,
                   to_ts: datetime | None = None) -> dict[str, Any]:
    f, t = _window(from_ts, to_ts, 24)
    b = _bucket(f, t)
    p = {"p": project_id, "f": f, "t": t}
    tw = "project_id = :p AND start_time BETWEEN :f AND :t"
    totals = (await q(request, f"""SELECT count(*) AS traces, coalesce(sum(total_cost), 0) AS cost,
        coalesce(sum(input_tokens + output_tokens), 0) AS tokens,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
        count(*) FILTER (WHERE level = 'ERROR') AS errors FROM pulse_traces WHERE {tw}""", **p))[0]
    obs_total = (await q(request, f"SELECT count(*) AS n FROM pulse_observations WHERE {tw} AND type <> 'http'",
                         **p))[0]["n"]
    return {
        "window": {"from": f, "to": t, "bucket": str(b)},
        "totals": {**totals, "observations": obs_total},
        "traces_by_name": await q(request, f"SELECT name, count(*) AS n FROM pulse_traces WHERE {tw} GROUP BY name "
                                           "ORDER BY n DESC LIMIT 10", **p),
        "model_costs": await q(request, f"""SELECT model, count(*) AS calls, sum(total_tokens) AS tokens,
            coalesce(sum(cost), 0) AS cost FROM pulse_observations WHERE {tw} AND model IS NOT NULL
            GROUP BY model ORDER BY cost DESC NULLS LAST""", **p),
        "observations_by_time": await q(request, f"""SELECT date_bin(:b, start_time,
            TIMESTAMPTZ '2000-01-01') AS bucket, level, count(*) AS n FROM pulse_observations
            WHERE {tw} AND type <> 'http' GROUP BY 1, 2 ORDER BY 1""", b=b, **p),
        "cost_by_time": await q(request, f"""SELECT date_bin(:b, start_time,
            TIMESTAMPTZ '2000-01-01') AS bucket, coalesce(model, 'n/a') AS model, coalesce(sum(cost), 0) AS cost,
            sum(total_tokens) AS tokens FROM pulse_observations WHERE {tw} AND model IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1""", b=b, **p),
        "latency_by_name": await q(request, f"""SELECT name, count(*) AS n,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms
            FROM pulse_observations WHERE {tw} AND type <> 'http' GROUP BY name ORDER BY p95_ms DESC LIMIT 12""", **p),
        "scores": await q(request, """SELECT name, source, count(*) AS n, avg(value) AS avg FROM pulse_scores
            WHERE project_id = :p AND created_at BETWEEN :f AND :t GROUP BY name, source ORDER BY n DESC""", **p),
    }


# ------------------------------------------------------------------------------------------- scores
class ScoreIn(BaseModel):
    trace_id: str
    observation_id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    value: float | None = None
    string_value: str | None = None
    source: str = "annotation"
    comment: str | None = None
    author: str | None = None


@app.post("/api/scores", status_code=201)
async def create_score(body: ScoreIn, request: Request, project_id: Project) -> dict[str, str]:
    if body.value is None and body.string_value is None:
        raise HTTPException(422, "value or string_value required")
    sid = str(uuid.uuid4())
    async with request.app.state.engine.begin() as conn:
        await conn.execute(insert(db.scores).values(
            id=sid, project_id=project_id, trace_id=body.trace_id, observation_id=body.observation_id, name=body.name,
            value=body.value, string_value=body.string_value,
            data_type="numeric" if body.value is not None else "categorical", source=body.source,
            comment=body.comment, author=body.author))
    return {"id": sid}


@app.get("/api/scores")
async def list_scores(request: Request, project_id: Project, name: str | None = None, source: str | None = None,
                      limit: int = Query(100, le=1000)) -> dict[str, Any]:
    where = ["s.project_id = :p"]
    params: dict[str, Any] = {"p": project_id, "lim": limit}
    if name:
        where.append("s.name = :name")
        params["name"] = name
    if source:
        where.append("s.source = :source")
        params["source"] = source
    w = " AND ".join(where)
    rows = await q(request, f"""SELECT s.*, t.name AS trace_name FROM pulse_scores s
        LEFT JOIN pulse_traces t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
        WHERE {w} ORDER BY s.created_at DESC LIMIT :lim""", **params)
    summary = await q(request, f"""SELECT s.name, s.source, count(*) AS n, avg(s.value) AS avg, min(s.value) AS min,
        max(s.value) AS max FROM pulse_scores s WHERE {w} GROUP BY s.name, s.source ORDER BY n DESC""", **params)
    return {"data": rows, "summary": summary}
