"""Composite market-data provider: ordered failover across vendor accounts.

Mirrors data/composite_calendar.py. Each "leg" is one account/key of one vendor
with its own rate bucket. On RateLimitExceeded/ProviderError the composite
records health, alerts on the transition, and advances to the next leg. It
raises ProviderError only when every leg fails (fail-CLOSE for signals).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.base import Candle, MarketDataProvider, Quote

logger = structlog.get_logger(__name__)

AlertCallback = Callable[[str], Awaitable[None]]


@dataclass
class MarketSourceHealth:
    name: str
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_attempt: datetime | None = None


class CompositeMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        legs: list[tuple[str, MarketDataProvider]],
        *,
        alert_callback: AlertCallback | None = None,
        mode: Literal["failover", "round_robin"] = "failover",
    ) -> None:
        if not legs:
            raise ValueError("CompositeMarketDataProvider needs at least one leg")
        self._legs = list(legs)
        self._alert = alert_callback
        self._mode = mode
        self._rr_index = 0
        self._health: dict[str, MarketSourceHealth] = {
            n: MarketSourceHealth(name=n) for n, _ in legs
        }

    async def _emit(self, message: str) -> None:
        if self._alert is not None:
            try:
                await self._alert(message)
            except Exception as exc:  # alert must never break data path
                logger.warning("market alert callback failed", error=str(exc))

    def _ordered_legs(self) -> list[tuple[str, MarketDataProvider]]:
        if self._mode == "round_robin" and len(self._legs) > 1:
            start = self._rr_index % len(self._legs)
            self._rr_index += 1
            return self._legs[start:] + self._legs[:start]
        return self._legs

    async def _attempt(
        self, op_name: str, call: Callable[[MarketDataProvider], Awaitable[object]]
    ) -> object:
        failed: list[str] = []
        for name, provider in self._ordered_legs():
            health = self._health[name]
            health.last_attempt = datetime.now(UTC)
            if failed:
                await self._emit(f"⚠️ Market fallback ({op_name}): {failed[-1]} gagal → coba {name}")
            try:
                result = await call(provider)
            except (RateLimitExceeded, ProviderError) as exc:
                health.last_error = str(exc)
                health.consecutive_failures += 1
                logger.warning("market leg failed", leg=name, op=op_name, error=str(exc))
                failed.append(name)
                continue
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.last_error = None
            if failed:
                await self._emit(
                    f"✅ Market fallback recovered: {' → '.join(failed)} gagal → {name} OK"
                )
            return result
        await self._emit("🚨 MARKET DATA: semua leg gagal")
        raise ProviderError(f"all market-data legs unavailable for {op_name}")

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, since: datetime, limit: int = 500
    ) -> list[Candle]:
        result = await self._attempt(
            "fetch_ohlcv", lambda p: p.fetch_ohlcv(symbol, timeframe, since, limit)
        )
        assert isinstance(result, list)
        return result

    async def fetch_quote(self, symbol: str) -> Quote:
        result = await self._attempt("fetch_quote", lambda p: p.fetch_quote(symbol))
        assert isinstance(result, Quote)
        return result

    async def fetch_spread(self, symbol: str) -> float | None:
        for name, provider in self._ordered_legs():
            try:
                spread = await provider.fetch_spread(symbol)
            except Exception as exc:  # one leg's failure must not abort the lookup
                logger.warning(
                    "market leg spread failed", leg=name, op="fetch_spread", error=str(exc)
                )
                continue
            if spread is not None:
                return spread
        return None

    def health_snapshot(self) -> dict[str, MarketSourceHealth]:
        return dict(self._health)

    def active_tier(self) -> str | None:
        best: tuple[datetime, str] | None = None
        for h in self._health.values():
            if h.last_success is not None and (best is None or h.last_success > best[0]):
                best = (h.last_success, h.name)
        return best[1] if best else None

    async def close(self) -> None:
        for _name, provider in self._legs:
            try:
                await provider.close()
            except Exception as exc:
                logger.warning("market leg close failed", error=str(exc))
