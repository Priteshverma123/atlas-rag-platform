"""Asynchronous usage ledger.

Lesson from LiteLLM's production incidents: never write spend logs synchronously in the request
path. Records are pushed to a Redis list (O(1), sub-ms) and a background task batch-inserts
them into Postgres. If Postgres is down, records stay in Redis and are retried.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from atlas.common.logging import get_logger
from atlas.db.models import LlmCall

log = get_logger(__name__)
QUEUE = "gw:spend:queue"


class SpendRecorder:
    def __init__(self, redis: Redis, engine: AsyncEngine | None, batch: int = 500, interval_s: float = 2.0) -> None:
        self.r = redis
        self.engine = engine
        self.batch = batch
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    async def record(self, **fields: Any) -> None:
        fields.setdefault("id", str(uuid.uuid4()))
        fields.setdefault("ts", datetime.now(UTC).isoformat())
        await self.r.rpush(QUEUE, orjson.dumps(fields))

    async def flush_once(self) -> int:
        if self.engine is None:
            return 0
        raw = await self.r.lpop(QUEUE, self.batch)
        if not raw:
            return 0
        rows = [orjson.loads(x) for x in raw]
        for row in rows:
            row["ts"] = datetime.fromisoformat(row["ts"])
            row["id"] = uuid.UUID(row["id"])
            for k in ("tenant_id", "api_key_id"):
                if row.get(k):
                    row[k] = uuid.UUID(row[k])
        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(LlmCall), rows)
        except Exception:
            log.exception("spend_flush_failed", rows=len(rows))
            await self.r.lpush(QUEUE, *raw)  # put back (order not important for a ledger)
            raise
        return len(rows)

    async def _loop(self) -> None:
        while True:
            try:
                n = await self.flush_once()
                if n < self.batch:
                    await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self.interval_s * 5)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="spend-flusher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        with contextlib.suppress(Exception):
            while await self.flush_once():
                pass
