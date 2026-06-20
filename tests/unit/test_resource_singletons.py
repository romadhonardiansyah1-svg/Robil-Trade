"""P0 #6 (audit A6): loop-aware process-scoped engine/redis singletons.

Verifies that within one event loop ``_get_engine`` / ``_get_redis`` return the
SAME instance on repeated calls, and that ``shutdown_process_resources`` disposes
and clears the caches so a subsequent lookup builds fresh. No live Postgres/Redis
is needed: ``create_async_engine`` and ``aioredis.from_url`` are both lazy and
never open a connection here.
"""

from __future__ import annotations

import pytest

from rtrade.persistence import db
from rtrade.persistence.db import (
    _get_engine,
    _get_redis,
    shutdown_process_resources,
)

# Lazily-constructed only — never connected to.
_DB_URL = "postgresql+asyncpg://user:pass@localhost:5432/rtrade_test"
_REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture(autouse=True)
async def _clean_caches() -> None:
    """Each test starts and ends with empty caches to stay deterministic."""
    db._ENGINE_CACHE.clear()
    db._REDIS_CACHE.clear()
    yield
    await shutdown_process_resources()


async def test_get_engine_returns_same_instance_within_loop() -> None:
    first = _get_engine(_DB_URL)
    second = _get_engine(_DB_URL)
    assert first is second
    assert len(db._ENGINE_CACHE) == 1


async def test_get_redis_returns_same_instance_within_loop() -> None:
    first = _get_redis(_REDIS_URL)
    second = _get_redis(_REDIS_URL)
    assert first is second
    assert len(db._REDIS_CACHE) == 1


async def test_shutdown_clears_caches_and_rebuilds_fresh() -> None:
    engine_before = _get_engine(_DB_URL)
    redis_before = _get_redis(_REDIS_URL)

    await shutdown_process_resources()

    assert db._ENGINE_CACHE == {}
    assert db._REDIS_CACHE == {}

    engine_after = _get_engine(_DB_URL)
    redis_after = _get_redis(_REDIS_URL)
    assert engine_after is not engine_before
    assert redis_after is not redis_before


async def test_distinct_urls_get_distinct_engines() -> None:
    a = _get_engine(_DB_URL)
    b = _get_engine("postgresql+asyncpg://user:pass@localhost:5432/other")
    assert a is not b
    assert len(db._ENGINE_CACHE) == 2
