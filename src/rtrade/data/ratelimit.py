"""Redis-backed token bucket rate limiter (PLAN §8.1).

Each data provider has its own bucket. The bucket is stored as a Redis key
containing the last-refill timestamp and current token count. Thread-safe
across processes via Redis atomic operations.

Provider limits (per PLAN / ADR-03):
- TwelveData free: 8 req/min → bucket 7/min (safety margin)
- ccxt Binance: 1200 req/min → bucket 1000/min
- Finnhub free: 60 req/min → bucket 50/min
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as aioredis

from rtrade.core.errors import RateLimitExceeded


@dataclass(frozen=True, slots=True)
class BucketConfig:
    """Token bucket parameters for a single provider."""

    name: str  # e.g. "twelvedata", "ccxt_binance"
    max_tokens: int  # bucket capacity
    refill_rate: float  # tokens per second
    refill_interval: float = 1.0  # seconds between refills

    @classmethod
    def per_minute(cls, name: str, rpm: int) -> BucketConfig:
        """Convenience: create from requests-per-minute."""
        return cls(name=name, max_tokens=rpm, refill_rate=rpm / 60.0)


# Pre-configured buckets for known providers.
TWELVEDATA_BUCKET = BucketConfig.per_minute("twelvedata", 7)
CCXT_BINANCE_BUCKET = BucketConfig.per_minute("ccxt_binance", 1000)
FINNHUB_BUCKET = BucketConfig.per_minute("finnhub", 50)
BINANCE_PUBLIC_BUCKET = BucketConfig.per_minute("binance_public", 500)


_LUA_ACQUIRE = """
-- Token bucket acquire script (atomic).
-- KEYS[1] = bucket key
-- ARGV[1] = max_tokens, ARGV[2] = refill_rate
-- `now` is taken from the Redis server clock (TIME) so the bucket is immune to
-- client clock-skew across processes (P0 fix #4).
-- Returns: 1 if acquired, 0 if rejected.

local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1]) or max_tokens
local last_refill = tonumber(data[2]) or now

-- Refill tokens based on elapsed time.
local elapsed = now - last_refill
local refill = elapsed * refill_rate
tokens = math.min(max_tokens, tokens + refill)

if tokens >= 1 then
    tokens = tokens - 1
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)  -- auto-cleanup
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', now)
    redis.call('EXPIRE', key, 120)
    return 0
end
"""


class RateLimiter:
    """Token-bucket rate limiter backed by Redis (PLAN §8.1).

    Usage::

        limiter = RateLimiter(redis_client)
        await limiter.acquire(TWELVEDATA_BUCKET)  # raises RateLimitExceeded if empty
    """

    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis
        self._script: object | None = None

    async def _get_script(self) -> object:
        if self._script is None:
            self._script = self._redis.register_script(_LUA_ACQUIRE)
        return self._script

    async def acquire(self, bucket: BucketConfig) -> None:
        """Consume one token. Raises RateLimitExceeded if bucket is empty."""
        script = await self._get_script()
        key = f"rtrade:ratelimit:{bucket.name}"
        result = await script(  # type: ignore[operator]
            keys=[key],
            args=[bucket.max_tokens, bucket.refill_rate],
        )
        if result == 0:
            raise RateLimitExceeded(
                f"rate limit exceeded for {bucket.name} "
                f"(max {bucket.max_tokens} tokens, {bucket.refill_rate:.1f}/s)"
            )

    async def tokens_remaining(self, bucket: BucketConfig) -> float:
        """Check how many tokens are available (for diagnostics)."""
        key = f"rtrade:ratelimit:{bucket.name}"
        # Use the Redis server clock (consistent with the acquire script, which
        # derives `now` from TIME) instead of the client clock.
        secs, micros = await self._redis.time()
        now = float(secs) + float(micros) / 1_000_000
        data = await self._redis.hmget(key, "tokens", "last_refill")
        tokens = float(data[0]) if data[0] is not None else float(bucket.max_tokens)
        last_refill = float(data[1]) if data[1] is not None else now
        elapsed = now - last_refill
        refill = elapsed * bucket.refill_rate
        return min(bucket.max_tokens, tokens + refill)
