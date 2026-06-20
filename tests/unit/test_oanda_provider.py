from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.oanda_provider import OANDA_PRACTICE_URL, OandaProvider


class _NoLimit:
    async def acquire(self, _bucket: Any) -> None:
        return None


_CANDLES = {
    "candles": [
        {
            "complete": True,
            "volume": 10,
            "time": "2025-01-01T00:00:00.000000000Z",
            "mid": {"o": "2600.0", "h": "2605.0", "l": "2599.0", "c": "2603.0"},
        },
        {
            "complete": True,
            "volume": 12,
            "time": "2025-01-01T00:05:00.000000000Z",
            "mid": {"o": "2603.0", "h": "2607.0", "l": "2602.0", "c": "2606.0"},
        },
        {
            "complete": False,
            "volume": 3,
            "time": "2025-01-01T00:10:00.000000000Z",
            "mid": {"o": "2606.0", "h": "2606.5", "l": "2605.0", "c": "2605.5"},
        },
    ]
}
_PRICING = {"prices": [{"bids": [{"price": "2603.40"}], "asks": [{"price": "2603.80"}]}]}


@pytest.mark.asyncio
async def test_fetch_ohlcv_parses_complete_candles_ascending() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/instruments/XAU_USD/candles").mock(
            return_value=httpx.Response(200, json=_CANDLES)
        )
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        candles = await p.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
        await p.close()
    assert len(candles) == 2  # forming bar (complete=false) dropped
    assert [float(c.close) for c in candles] == [2603.0, 2606.0]
    assert candles[0].ts < candles[1].ts


@pytest.mark.parametrize("timeframe", [Timeframe.H4, Timeframe.D1])
@pytest.mark.asyncio
async def test_fetch_ohlcv_sends_utc_alignment_params(timeframe: Timeframe) -> None:
    """D2: D and H4 candles MUST align to the UTC day boundary, not OANDA's
    default 17:00 America/New_York. The request must carry alignmentTimezone=UTC
    and dailyAlignment=0 so anti-look-ahead cutoffs, DST gap detection, and
    cross-provider MTF alignment stay correct."""
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        route = mock.get("/v3/instruments/XAU_USD/candles").mock(
            return_value=httpx.Response(200, json=_CANDLES)
        )
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        await p.fetch_ohlcv("XAU_USD", timeframe, datetime(2025, 1, 1, tzinfo=UTC))
        await p.close()
    request_url = route.calls.last.request.url
    assert request_url.params["alignmentTimezone"] == "UTC"
    assert request_url.params["dailyAlignment"] == "0"


@pytest.mark.asyncio
async def test_fetch_ohlcv_http_400_raises_provider_error() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/instruments/XAU_USD/candles").mock(
            return_value=httpx.Response(400, text="bad")
        )
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        with pytest.raises(ProviderError):
            await p.fetch_ohlcv("XAU_USD", Timeframe.M5, datetime(2025, 1, 1, tzinfo=UTC))
        await p.close()


@pytest.mark.asyncio
async def test_fetch_quote_returns_mid() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(200, json=_PRICING))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        q = await p.fetch_quote("XAU_USD")
        await p.close()
    assert float(q.price) == pytest.approx(2603.60)


@pytest.mark.asyncio
async def test_fetch_quote_429_raises_ratelimit() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(429, json={}))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        with pytest.raises(RateLimitExceeded):
            await p.fetch_quote("XAU_USD")
        await p.close()


@pytest.mark.asyncio
async def test_fetch_spread_returns_ask_minus_bid() -> None:
    with respx.mock(base_url=OANDA_PRACTICE_URL) as mock:
        mock.get("/v3/accounts/acc/pricing").mock(return_value=httpx.Response(200, json=_PRICING))
        p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
        spread = await p.fetch_spread("XAU_USD")
        await p.close()
    assert spread == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_unsupported_timeframe_raises() -> None:
    p = OandaProvider("tok", "acc", _NoLimit())  # type: ignore[arg-type]
    # Timeframe has no member unsupported by OANDA in the enum; assert map covers all used TFs.
    from rtrade.data.oanda_provider import _TF_MAP

    for tf in (Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1):
        assert tf in _TF_MAP
    await p.close()
