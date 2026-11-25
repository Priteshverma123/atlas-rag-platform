"""API keys: generation, hashing and cached lookup.

Keys look like ``atl_<32 urlsafe chars>``; only the SHA-256 is stored (a fast hash is fine for
high-entropy random keys, unlike passwords). Lookups are cached in Redis for 60 s so auth adds
~0.2 ms instead of a DB round trip; revocation takes effect within the TTL (documented trade-off).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import asdict, dataclass, field

import orjson
from redis.asyncio import Redis
from sqlalchemy import select

from atlas.db.models import ApiKey, Tenant
from atlas.db.session import admin_session

PREFIX = "atl_"
_CACHE_TTL = 60


def generate_key() -> str:
    return PREFIX + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass(slots=True)
class KeyInfo:
    key_id: str
    tenant_id: str
    scopes: list[str] = field(default_factory=list)
    rpm: int = 0
    tpm: int = 0
    budget_usd_day: float | None = None
    tenant_budget_month: float | None = None
    user_external_id: str | None = None

    def has(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes


async def lookup_key(redis: Redis, raw_key: str) -> KeyInfo | None:
    if not raw_key.startswith(PREFIX):
        return None
    h = hash_key(raw_key)
    ck = f"auth:key:{h}"
    if (cached := await redis.get(ck)) is not None:
        data = orjson.loads(cached)
        return KeyInfo(**data) if data else None
    async with admin_session() as s:
        row = (await s.execute(
            select(ApiKey, Tenant.monthly_budget_usd).join(Tenant, Tenant.id == ApiKey.tenant_id)
            .where(ApiKey.key_hash == h, ApiKey.revoked_at.is_(None)))).first()
    info = None
    if row is not None:
        k, tenant_budget = row
        info = KeyInfo(key_id=str(k.id), tenant_id=str(k.tenant_id), scopes=list(k.scopes), rpm=k.rpm, tpm=k.tpm,
                       budget_usd_day=k.budget_usd_day, tenant_budget_month=tenant_budget,
                       user_external_id=k.user_external_id)
    # negative results are cached too (brute-force protection), but briefly
    await redis.set(ck, orjson.dumps(asdict(info) if info else None), ex=_CACHE_TTL if info else 10)
    return info


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


async def create_key(tenant_id: uuid.UUID, name: str, scopes: list[str], *, rpm: int = 600, tpm: int = 400_000,
                     budget_usd_day: float | None = None, user_external_id: str | None = None) -> tuple[str, ApiKey]:
    raw = generate_key()
    async with admin_session() as s:
        row = ApiKey(tenant_id=tenant_id, name=name, key_prefix=raw[:8], key_hash=hash_key(raw), scopes=scopes,
                     rpm=rpm, tpm=tpm, budget_usd_day=budget_usd_day, user_external_id=user_external_id)
        s.add(row)
    return raw, row
