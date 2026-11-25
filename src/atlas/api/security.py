"""API authentication + identity resolution.

Trust model for user identity and ACL groups (they control what documents a user can see):
  * a key bound to a user (``user_external_id``) → that user, groups from the ``users`` table;
  * a key with the ``impersonate`` scope (a trusted backend embedding Atlas) may assert the end
    user with ``X-Atlas-User`` and groups with ``X-Atlas-Groups``;
  * otherwise requests are anonymous: no memory, and only public (ACL-less) chunks are visible.
Clients can never widen their own ACL groups.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atlas.common.errors import Forbidden, RateLimited, Unauthorized
from atlas.db.apikeys import lookup_key
from atlas.db.models import User
from atlas.db.session import tenant_session
from atlas.gateway.limits.ratelimit import RateLimiter
from atlas.kb.context import RequestContext


async def _upsert_user(tenant_id: str, external_id: str, groups: list[str] | None) -> tuple[uuid.UUID, list[str]]:
    async with tenant_session(tenant_id) as s:
        stmt = pg_insert(User).values(id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), external_id=external_id,
                                      groups=groups or [])
        stmt = stmt.on_conflict_do_update(index_elements=["tenant_id", "external_id"],
                                          set_={"groups": stmt.excluded.groups}) if groups is not None else \
            stmt.on_conflict_do_nothing(index_elements=["tenant_id", "external_id"])
        await s.execute(stmt)
        row = (await s.execute(select(User.id, User.groups).where(User.external_id == external_id))).one()
    return row[0], list(row[1])


async def request_context(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_atlas_user: Annotated[str | None, Header()] = None,
    x_atlas_groups: Annotated[str | None, Header()] = None,
) -> RequestContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    info = await lookup_key(request.app.state.c.redis, authorization[7:].strip())
    if info is None:
        raise Unauthorized("invalid or revoked api key")
    limiter: RateLimiter = request.app.state.limiter
    d = await limiter.take(f"api:{info.key_id}", info.rpm)
    if not d.allowed:
        raise RateLimited("rate limit exceeded", retry_after=d.retry_after_s)

    ctx = RequestContext(tenant_id=info.tenant_id, key_id=info.key_id, scopes=info.scopes)
    if info.user_external_id:
        ctx.user_external_id = info.user_external_id
        ctx.user_id, ctx.groups = await _upsert_user(info.tenant_id, info.user_external_id, None)
    elif x_atlas_user:
        if not info.has("impersonate"):
            raise Forbidden("asserting X-Atlas-User requires the 'impersonate' scope")
        groups = [g.strip() for g in (x_atlas_groups or "").split(",") if g.strip()] if x_atlas_groups is not None \
            else None
        ctx.user_external_id = x_atlas_user
        ctx.user_id, ctx.groups = await _upsert_user(info.tenant_id, x_atlas_user, groups)
    else:
        # Service keys without an end user get a synthetic per-key principal so conversations
        # persist, but with NO ACL groups: they only ever see public (ACL-less) documents.
        ctx.user_external_id = f"apikey:{info.key_id}"
        ctx.user_id, _ = await _upsert_user(info.tenant_id, ctx.user_external_id, None)
        ctx.groups = []
    return ctx


def require(scope: str):  # type: ignore[no-untyped-def]
    async def dep(ctx: Annotated[RequestContext, Depends(request_context)]) -> RequestContext:
        if not ctx.has(scope):
            raise Forbidden(f"api key lacks the '{scope}' scope")
        return ctx
    return dep


Ctx = Annotated[RequestContext, Depends(request_context)]
ReadCtx = Annotated[RequestContext, Depends(require("read"))]
WriteCtx = Annotated[RequestContext, Depends(require("write"))]
AdminCtx = Annotated[RequestContext, Depends(require("admin"))]
