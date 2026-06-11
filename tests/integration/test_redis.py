"""P0-T2 AC: redis reachable and answering PING."""

import socket

import pytest
import redis.asyncio as aioredis
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration


async def test_redis_ping(test_redis_url: str) -> None:
    # Quick TCP probe so the test skips (not fails) when the stack is down.
    url = make_url(test_redis_url.replace("redis://", "redis+dummy://", 1))
    host, port = url.host or "localhost", url.port or 6379
    try:
        with socket.create_connection((host, port), timeout=2.0):
            pass
    except OSError:
        pytest.skip(f"redis not reachable at {host}:{port} — run `docker compose up -d`")

    client = aioredis.from_url(test_redis_url)
    try:
        assert await client.ping() is True
    finally:
        await client.aclose()
