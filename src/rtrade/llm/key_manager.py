"""Multi-key rotation manager with Redis-based cooldown (PLAN P3-T1).

Handles:
- Round-robin API key selection across providers
- Redis-backed cooldown: key that gets 429 → cooldown TTL via Redis SET
- Daily budget tracking with Telegram alert at 80%
- Thread-safe key selection

Usage:
    mgr = KeyManager(redis_client, keys_by_provider, daily_budget=1.0)
    key = await mgr.get_next_key("gemini")  # returns available key
    await mgr.report_rate_limit("gemini", key)  # marks key as cooling down
    await mgr.report_cost("gemini", key, 0.003)  # tracks spending
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AllKeysExhaustedError(Exception):
    """All API keys for a provider are in cooldown."""


class KeyManager:
    """Multi-key rotation with Redis cooldown and budget tracking.

    Keys are rotated round-robin. When a key hits a 429 rate limit,
    it enters a cooldown period tracked in Redis. If all keys are
    in cooldown, raises AllKeysExhaustedError.
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        keys_by_provider: dict[str, list[str]] | None = None,
        *,
        cooldown_seconds: int = 60,
        daily_budget_usd: float = 1.0,
        budget_alert_pct: float = 0.8,
    ) -> None:
        self._redis = redis_client
        self._keys = keys_by_provider or {}
        self._cooldown_sec = cooldown_seconds
        self._daily_budget = daily_budget_usd
        self._budget_alert_pct = budget_alert_pct

        # Round-robin index per provider.
        self._index: dict[str, int] = dict.fromkeys(self._keys, 0)

        # In-memory fallback when Redis is not available.
        self._cooldowns: dict[str, float] = {}  # key -> expiry epoch
        self._daily_cost: dict[str, float] = {}  # date_str -> total cost
        self._alert_sent: set[str] = set()  # date_strs already alerted

    async def get_next_key(self, provider: str) -> str:
        """Get the next available API key for a provider.

        Rotates round-robin, skipping keys in cooldown.

        Raises:
            AllKeysExhaustedError: If all keys are in cooldown.
            KeyError: If provider has no keys configured.
        """
        keys = self._keys.get(provider, [])
        if not keys:
            raise KeyError(f"no API keys configured for provider '{provider}'")

        n = len(keys)
        start_idx = self._index.get(provider, 0) % n

        for i in range(n):
            idx = (start_idx + i) % n
            key = keys[idx]
            if not await self._is_cooling_down(provider, key):
                self._index[provider] = (idx + 1) % n
                return key

        raise AllKeysExhaustedError(
            f"all {n} keys for provider '{provider}' are in cooldown ({self._cooldown_sec}s)"
        )

    async def report_rate_limit(self, provider: str, key: str) -> None:
        """Mark a key as rate-limited (429 response).

        Puts the key into cooldown for `cooldown_seconds`.
        """
        cooldown_key = f"rtrade:cooldown:{provider}:{_key_id(key)}"

        if self._redis is not None:
            try:
                await self._redis.setex(cooldown_key, self._cooldown_sec, "1")
                logger.warning(
                    "key rate-limited, entering cooldown",
                    provider=provider,
                    key_masked=_mask(key),
                    cooldown_sec=self._cooldown_sec,
                )
                return
            except Exception as exc:
                logger.error(
                    "redis cooldown set failed, using memory",
                    error=str(exc),
                )

        # Fallback: in-memory cooldown.
        import time

        self._cooldowns[cooldown_key] = time.time() + self._cooldown_sec
        logger.warning(
            "key rate-limited (memory cooldown)",
            provider=provider,
            key_masked=_mask(key),
        )

    async def report_cost(self, provider: str, key: str, cost_usd: float) -> None:
        """Track LLM cost and check budget alert threshold."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        date_key = f"rtrade:cost:{today}"

        if self._redis is not None:
            try:
                total = await self._redis.incrbyfloat(date_key, cost_usd)
                # Set expiry on the key (25h to ensure daily reset).
                await self._redis.expire(date_key, 90000)
            except Exception:
                total = self._daily_cost.get(today, 0) + cost_usd
                self._daily_cost[today] = total
        else:
            total = self._daily_cost.get(today, 0) + cost_usd
            self._daily_cost[today] = total

        # Check budget alert.
        if total >= self._daily_budget * self._budget_alert_pct and today not in self._alert_sent:
            self._alert_sent.add(today)
            logger.warning(
                "LLM daily budget alert",
                total_usd=f"{total:.4f}",
                budget_usd=f"{self._daily_budget:.2f}",
                pct=f"{total / self._daily_budget * 100:.0f}%",
            )

    async def get_daily_cost(self) -> float:
        """Get today's total LLM cost."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        date_key = f"rtrade:cost:{today}"

        if self._redis is not None:
            try:
                val = await self._redis.get(date_key)
                return float(val) if val else 0.0
            except Exception:
                pass

        return self._daily_cost.get(today, 0.0)

    async def _is_cooling_down(self, provider: str, key: str) -> bool:
        """Check if a key is currently in cooldown."""
        cooldown_key = f"rtrade:cooldown:{provider}:{_key_id(key)}"

        if self._redis is not None:
            try:
                val = await self._redis.get(cooldown_key)
                return val is not None
            except Exception:
                pass

        # Fallback: check in-memory.
        import time

        expiry = self._cooldowns.get(cooldown_key, 0)
        if expiry > time.time():
            return True
        # Clean up expired.
        self._cooldowns.pop(cooldown_key, None)
        return False

    @property
    def providers(self) -> list[str]:
        """List of configured providers."""
        return list(self._keys.keys())

    def key_count(self, provider: str) -> int:
        """Number of keys for a provider."""
        return len(self._keys.get(provider, []))


def _key_id(key: str) -> str:
    """Create a unique, stable identifier from an API key.

    Uses SHA-256 hash truncated to 12 chars for uniqueness
    without exposing the key.
    """
    import hashlib

    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _mask(key: str) -> str:
    """Mask an API key for logging (show first 6 + last 4 chars)."""
    if len(key) <= 12:
        return key[:3] + "***"
    return key[:6] + "***" + key[-4:]
