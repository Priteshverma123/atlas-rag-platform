"""Internal client for atlas-gateway (east-west, internal service key + tenant headers).

Every Atlas module calls LLMs through *routes* (``fast``, ``answer``, ``deep``, ``judge``), never
through a vendor SDK, so model choice, fallbacks, caching and cost accounting are configuration.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from atlas.common.errors import UpstreamError
from atlas.common.settings import Settings

T = TypeVar("T", bound=BaseModel)
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


@dataclass(slots=True)
class LLMResult:
    text: str
    provider: str | None = None
    model: str | None = None
    cost_usd: float = 0.0
    cache: str = "miss"
    usage: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class StreamDelta:
    text: str = ""
    done: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


def _strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured-output backends require additionalProperties=false and all props required."""
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        props = schema.get("properties", {})
        schema["required"] = list(props)
        for p in props.values():
            _strictify(p)
    if schema.get("type") == "array" and isinstance(schema.get("items"), dict):
        _strictify(schema["items"])
    for d in schema.get("$defs", {}).values():
        _strictify(d)
    return schema


class LLMClient:
    def __init__(self, settings: Settings, *, timeout_s: float = 120.0) -> None:
        self.base = settings.gateway_url.rstrip("/")
        self.key = settings.gateway_internal_key.get_secret_value()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=5.0))

    def _headers(self, tenant_id: str | None, tags: list[str] | None, bypass_cache: bool | str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.key}"}
        if tenant_id:
            h["x-atlas-tenant"] = tenant_id
        if tags:
            h["x-atlas-cache-tags"] = ",".join(tags)
        if bypass_cache:
            h["x-atlas-cache"] = bypass_cache if isinstance(bypass_cache, str) else "bypass"
        return h

    async def chat(self, route: str, messages: list[dict[str, Any]], *, tenant_id: str | None = None,
                   max_tokens: int | None = None, response_format: dict[str, Any] | None = None,
                   cache_tags: list[str] | None = None, bypass_cache: bool | str = False) -> LLMResult:
        body: dict[str, Any] = {"model": route, "messages": messages}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if response_format:
            body["response_format"] = response_format
        try:
            r = await self._http.post(f"{self.base}/v1/chat/completions", json=body,
                                      headers=self._headers(tenant_id, cache_tags, bypass_cache))
        except httpx.HTTPError as exc:
            raise UpstreamError(f"gateway unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise UpstreamError(f"gateway {r.status_code}: {r.text[:500]}")
        data = r.json()
        return LLMResult(text=data["choices"][0]["message"].get("content") or "", provider=data.get("provider"),
                         model=data.get("model"), cost_usd=data.get("cost_usd") or 0.0,
                         cache=data.get("cache") or "miss", usage=data.get("usage") or {})

    async def structured(self, route: str, messages: list[dict[str, Any]], schema: type[T], *,
                         tenant_id: str | None = None, max_tokens: int | None = None) -> tuple[T, LLMResult]:
        """Typed structured output. One repair retry if a provider returns invalid JSON."""
        rf = {"type": "json_schema", "json_schema": {"name": schema.__name__, "strict": True,
                                                       "schema": _strictify(schema.model_json_schema())}}
        res = await self.chat(route, messages, tenant_id=tenant_id, max_tokens=max_tokens, response_format=rf)
        for attempt in range(3):
            try:
                return schema.model_validate_json(_extract_json(res.text)), res
            except (ValidationError, ValueError) as exc:
                if attempt == 2:
                    raise UpstreamError(f"invalid structured output for {schema.__name__}: {exc}") from exc
                if attempt == 0 and res.cache != "miss":
                    # the invalid value came from cache: re-run the original request and overwrite the entry
                    res = await self.chat(route, messages, tenant_id=tenant_id, max_tokens=max_tokens,
                                          response_format=rf, bypass_cache="refresh")
                    continue
                repair = messages + [{"role": "assistant", "content": res.text},
                                     {"role": "user", "content": f"That was not valid JSON for the schema. "
                                                                 f"Error: {exc}. Reply with only the corrected JSON."}]
                res = await self.chat(route, repair, tenant_id=tenant_id, max_tokens=max_tokens, response_format=rf,
                                      bypass_cache=True)
        raise AssertionError("unreachable")

    async def stream(self, route: str, messages: list[dict[str, Any]], *, tenant_id: str | None = None,
                     max_tokens: int | None = None, cache_tags: list[str] | None = None) -> AsyncIterator[StreamDelta]:
        body: dict[str, Any] = {"model": route, "messages": messages, "stream": True}
        if max_tokens:
            body["max_tokens"] = max_tokens
        async with self._http.stream("POST", f"{self.base}/v1/chat/completions", json=body,
                                     headers=self._headers(tenant_id, cache_tags, False)) as r:
            if r.status_code >= 400:
                raise UpstreamError(f"gateway {r.status_code}: {(await r.aread())[:500]!r}")
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    return
                data = json.loads(payload)
                if "error" in data:
                    raise UpstreamError(data["error"].get("message", "stream error"))
                choices = data.get("choices") or []
                delta = choices[0].get("delta", {}).get("content", "") if choices else ""
                finished = bool(choices and choices[0].get("finish_reason"))
                meta = {"usage": data.get("usage"), **(data.get("x_atlas") or {})} if finished else {}
                yield StreamDelta(text=delta or "", done=finished, meta=meta)

    async def invalidate_cache(self, tenant_id: str, tags: list[str]) -> None:
        """Drop gateway cache entries grounded on changed/deleted documents."""
        try:
            await self._http.post(f"{self.base}/admin/cache/invalidate", json={"tenant_id": tenant_id, "tags": tags},
                                  headers=self._headers(None, None, False))
        except httpx.HTTPError:
            pass  # entries also expire by TTL; invalidation is best-effort

    async def aclose(self) -> None:
        await self._http.aclose()


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    if text.startswith("{"):
        return text
    m = _JSON_BLOCK.search(text)
    if not m:
        raise ValueError("no JSON object in response")
    return m.group(0)
