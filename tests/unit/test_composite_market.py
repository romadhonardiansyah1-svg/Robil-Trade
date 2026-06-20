from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.base import Candle, MarketDataProvider, Quote
from rtrade.data.composite_market import CompositeMarketDataProvider


def _candle() -> Candle:
    return Candle(
        symbol="XAU_USD",
        timeframe=Timeframe.M5,
        ts=datetime(2025, 1, 1, tzinfo=UTC),
        open=Decimal("2600"),
        high=Decimal("2601"),
        low=Decimal("2599"),
        close=Decimal("2600"),
    )


class _Leg(MarketDataProvider):
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.ohlcv_calls = 0
        self.closed = False

    async def fetch_ohlcv(
        self, symbol: str, timeframe: Timeframe, since: datetime, limit: int = 500
    ) -> list[Candle]:
        self.ohlcv_calls += 1
        if self.fail is not None:
            raise self.fail
        return [_candle()]

    async def fetch_quote(self, symbol: str) -> Quote:
        if self.fail is not None:
            raise self.fail
        return Quote(symbol=symbol, price=Decimal("2600"), ts=datetime.now(UTC))

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_first_leg_used_when_healthy() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    out = await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert len(out) == 1
    assert a.ohlcv_calls == 1 and b.ohlcv_calls == 0
    assert comp.active_tier() == "a"


@pytest.mark.asyncio
async def test_failover_to_next_leg_on_ratelimit() -> None:
    alerts: list[str] = []

    async def cb(msg: str) -> None:
        alerts.append(msg)

    a = _Leg(fail=RateLimitExceeded("429"))
    b = _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)], alert_callback=cb)
    out = await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert len(out) == 1
    assert a.ohlcv_calls == 1 and b.ohlcv_calls == 1
    assert comp.health_snapshot()["a"].consecutive_failures == 1
    assert any("a" in m for m in alerts)


@pytest.mark.asyncio
async def test_all_legs_fail_raises_provider_error() -> None:
    a = _Leg(fail=ProviderError("down"))
    b = _Leg(fail=RateLimitExceeded("429"))
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    with pytest.raises(ProviderError):
        await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))


@pytest.mark.asyncio
async def test_round_robin_distributes_calls() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)], mode="round_robin")
    for _ in range(4):
        await comp.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
    assert a.ohlcv_calls == 2 and b.ohlcv_calls == 2


@pytest.mark.asyncio
async def test_close_closes_all_legs() -> None:
    a, b = _Leg(), _Leg()
    comp = CompositeMarketDataProvider([("a", a), ("b", b)])
    await comp.close()
    assert a.closed and b.closed
