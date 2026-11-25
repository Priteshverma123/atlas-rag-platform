"""Async engine + tenant-scoped sessions.

Every unit of work runs inside a transaction that first sets ``app.tenant_id`` with
``set_config(..., is_local=true)`` so the value dies with the transaction. This works with
PgBouncer in transaction-pooling mode and makes RLS the last line of defence: even a bug that
forgets a ``WHERE tenant_id = ...`` cannot leak another tenant's rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from atlas.common.settings import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    s = get_settings()
    return create_async_engine(s.database_url, pool_size=10, max_overflow=20, pool_pre_ping=True,
                               pool_recycle=1800)


@lru_cache
def get_admin_engine() -> AsyncEngine:
    """Owner role: bypasses RLS. Used only for API-key lookup, tenant bootstrap and migrations."""
    return create_async_engine(get_settings().database_admin_url, pool_size=3, pool_pre_ping=True)


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache
def _admin_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_admin_engine(), expire_on_commit=False)


@asynccontextmanager
async def tenant_session(tenant_id: uuid.UUID | str) -> AsyncIterator[AsyncSession]:
    async with _sessionmaker()() as session, session.begin():
        await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        yield session


@asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    async with _admin_sessionmaker()() as session, session.begin():
        yield session
