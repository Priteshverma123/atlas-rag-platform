"""Exact-match response cache with tag-based invalidation.

Key = sha256 of the canonical request (route/model, messages, tools, response_format, output
limit) scoped by tenant. Tags (e.g. document ids an answer was grounded on) are stored in Redis
sets so that changing a document invalidates every cached answer that used it.
"""

from __future__ import annotations

from redis.asyncio import Redis

from atlas.common.text import canonical_json_hash
from atlas.gateway.schemas import ChatRequest, ChatResponse


def request_fingerprint(req: ChatRequest, route: str) -> str:
    return canonical_json_hash({
        "route": route,
        "messages": [m.model_dump(exclude_none=True) for m in req.messages],
        "tools": req.tools, "tool_choice": req.tool_choice, "response_format": req.response_format,
        "max": req.output_limit(), "temperature": req.temperature,
    })


class ExactCache:
    def __init__(self, redis: Redis) -> None:
        self.r = redis

    @staticmethod
    def _key(tenant: str, fp: str) -> str:
        return f"gw:exact:{tenant}:{fp}"

    async def get(self, tenant: str, fp: str) -> ChatResponse | None:
        raw = await self.r.get(self._key(tenant, fp))
        return ChatResponse.model_validate_json(raw) if raw else None

    async def set(self, tenant: str, fp: str, resp: ChatResponse, ttl_s: int, tags: list[str]) -> None:
        key = self._key(tenant, fp)
        pipe = self.r.pipeline()
        pipe.set(key, resp.model_dump_json(), ex=ttl_s)
        for tag in tags:
            pipe.sadd(f"gw:tag:{tenant}:{tag}", key)
            pipe.expire(f"gw:tag:{tenant}:{tag}", ttl_s)
        await pipe.execute()

    async def invalidate_tags(self, tenant: str, tags: list[str]) -> int:
        n = 0
        for tag in tags:
            tkey = f"gw:tag:{tenant}:{tag}"
            keys = await self.r.smembers(tkey)
            if keys:
                n += await self.r.delete(*keys)
            await self.r.delete(tkey)
        return n
