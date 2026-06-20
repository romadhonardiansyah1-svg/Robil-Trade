"""Async engine / session factory. One engine per process; sessions are cheap.

P0 fix #6 (audit A6): process-scoped, *loop-aware* engine/redis singletons.

The long-running worker runs on a single event loop, so a per-call
``create_async_engine`` / ``aioredis.from_url`` churns connections needlessly.
``_get_engine`` / ``_get_redis`` cache one resource per (running event loop,
url) pair. Keying on the loop keeps pytest-asyncio safe: each test gets a fresh
loop and therefore a fresh engine, so a connection/Future never leaks across
loops ("attached to a different loop"). The worker (one loop) reuses a single
engine for its whole lifetime; ``shutdown_process_resources`` disposes them all
on graceful shutdown.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    if not database_url.startswith("postgresql+asyncpg://"):
        # Fail fast: the codebase assumes asyncpg (JSONB, ON CONFLICT, timescale).
        raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver")
    return create_async_engine(database_url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# --- Loop-aware process-scoped singletons (P0 #6) ------------------------------
# Each entry keeps a *reference* to its owning loop alongside the resource. Holding
# the loop reference is deliberate: it prevents the loop object from being garbage
# collected, which in turn guarantees ``id(loop)`` cannot be reused by a different
# live loop while a cache entry exists (otherwise two loops could collide on the
# same key).
_ENGINE_CACHE: dict[tuple[int, str], tuple[asyncio.AbstractEventLoop, AsyncEngine]] = {}
_REDIS_CACHE: dict[tuple[int, str], tuple[asyncio.AbstractEventLoop, aioredis.Redis]] = {}


def _get_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Return a process-scoped AsyncEngine for the current event loop.

    Creates (and caches) a fresh engine when none exists for this loop, or when
    the cached engine's loop has since closed. Only ever called from inside
    async functions, so a running loop is guaranteed to exist.
    """
    loop = asyncio.get_running_loop()
    key = (id(loop), database_url)
    cached = _ENGINE_CACHE.get(key)
    if cached is not None:
        cached_loop, engine = cached
        if not cached_loop.is_closed():
            return engine
    engine = create_engine(database_url, echo=echo)
    _ENGINE_CACHE[key] = (loop, engine)
    return engine


def _get_redis(redis_url: str) -> aioredis.Redis:
    """Return a process-scoped redis.asyncio client for the current event loop.

    Mirrors ``_get_engine``: one client per (loop, url), recreated if the cached
    client's loop has closed. Only ever called from inside async functions.
    """
    loop = asyncio.get_running_loop()
    key = (id(loop), redis_url)
    cached = _REDIS_CACHE.get(key)
    if cached is not None:
        cached_loop, client = cached
        if not cached_loop.is_closed():
            return client
    client = aioredis.from_url(redis_url)
    _REDIS_CACHE[key] = (loop, client)
    return client


async def shutdown_process_resources() -> None:
    """Dispose every cached engine and close every cached redis client.

    Wired into the worker's graceful shutdown. Best-effort: a failure disposing
    one resource must not prevent the rest from being cleaned up. Clears both
    caches so a subsequent ``_get_engine`` / ``_get_redis`` builds fresh.
    """
    for _loop, engine in list(_ENGINE_CACHE.values()):
        try:
            await engine.dispose()
        except Exception:
            pass
    _ENGINE_CACHE.clear()
    for _loop, client in list(_REDIS_CACHE.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    _REDIS_CACHE.clear()
