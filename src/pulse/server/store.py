"""Ingest pipeline: OTLP request → Redis list (durable buffer, sub-ms) → batch flusher → Postgres.

The receiver never touches the database, so a slow or restarting DB cannot back-pressure the
instrumented applications. Payloads that fail to decode are moved to a dead-letter list.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import structlog
from redis.asyncio import Redis
from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from pulse.server import db
from pulse.server.normalize import normalize
from pulse.server.otlp import decode

log = structlog.get_logger("pulse.ingest")
QUEUE, DLQ = "pulse:ingest", "pulse:ingest:dead"


class Ingestor:
    def __init__(self, engine: AsyncEngine, redis: Redis | None, *, max_queue: int = 50_000) -> None:
        self.engine, self.redis, self.max_queue = engine, redis, max_queue
        self._task: asyncio.Task[None] | None = None
        self.stats = {"spans": 0, "batches": 0, "errors": 0}

    async def accept(self, project_id: str, body: bytes, content_type: str, encoding: str) -> bool:
        """Returns False when the buffer is full (caller answers 429 + Retry-After)."""
        if self.redis is None:
            await self.process([(project_id, body, content_type, encoding)])
            return True
        if await self.redis.llen(QUEUE) >= self.max_queue:
            return False
        await self.redis.rpush(QUEUE, orjson.dumps({"p": project_id, "ct": content_type, "ce": encoding,
                                                    "b": base64.b64encode(body).decode()}))
        return True

    async def process(self, payloads: list[tuple[str, bytes, str, str]]) -> int:
        rows: dict[tuple[str, str], dict[str, Any]] = {}
        score_rows: list[dict[str, Any]] = []
        for project_id, body, ct, ce in payloads:
            for span in decode(body, ct, ce):
                row, scores = normalize(project_id, span)
                rows[(project_id, row["span_id"])] = row
                score_rows.extend(scores)
        if not rows:
            return 0
        async with self.engine.begin() as conn:
            stmt = insert(db.observations).values(list(rows.values()))
            update_cols = {c.name: stmt.excluded[c.name] for c in db.observations.columns
                           if c.name not in ("project_id", "span_id")}
            await conn.execute(stmt.on_conflict_do_update(index_elements=["project_id", "span_id"], set_=update_cols))
            for s in score_rows:
                s["id"] = str(uuid.uuid4())
            if score_rows:
                await conn.execute(insert(db.scores).values(score_rows).on_conflict_do_nothing())
            by_project: dict[str, set[str]] = {}
            for (pid, _), r in rows.items():
                by_project.setdefault(pid, set()).add(r["trace_id"])
            for pid, tids in by_project.items():
                await conn.execute(text(db.AGGREGATE_TRACES), {"project_id": pid, "trace_ids": list(tids)})
        self.stats["spans"] += len(rows)
        self.stats["batches"] += 1
        return len(rows)

    async def flush_once(self, batch: int = 100) -> int:
        assert self.redis is not None
        raw = await self.redis.lpop(QUEUE, batch)
        if not raw:
            return 0
        payloads = []
        for item in raw:
            d = orjson.loads(item)
            payloads.append((d["p"], base64.b64decode(d["b"]), d["ct"], d["ce"]))
        try:
            return await self.process(payloads)
        except Exception:
            self.stats["errors"] += 1
            log.exception("pulse_flush_failed", payloads=len(payloads))
            # Isolate poison payloads: retry one by one, dead-letter the ones that still fail.
            for p, item in zip(payloads, raw, strict=True):
                try:
                    await self.process([p])
                except Exception:
                    await self.redis.rpush(DLQ, item)
            return 0

    async def _loop(self) -> None:
        while True:
            try:
                if await self.flush_once() == 0:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pulse_flusher_error")
                await asyncio.sleep(2)

    def start(self) -> None:
        if self.redis is not None:
            self._task = asyncio.create_task(self._loop(), name="pulse-flusher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            with contextlib.suppress(Exception):
                while await self.flush_once():
                    pass

    async def retention(self, days: int) -> None:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        async with self.engine.begin() as conn:
            await conn.execute(delete(db.observations).where(db.observations.c.start_time < cutoff))
            await conn.execute(delete(db.traces).where(db.traces.c.start_time < cutoff))
