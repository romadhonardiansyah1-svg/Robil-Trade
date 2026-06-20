"""Bug condition exploration test for BUG 5 — wasteful incremental ingest.

Property 6 (Bug Condition): Fresh Watermark Skips Provider.

This test encodes the EXPECTED post-fix behavior described in design.md
(Correctness Property 6, Bug Condition C5) and bugfix.md requirements 1.10, 2.10:

    For an _ingest_incremental() call where the latest candle is fresher than one
    bar, the fixed code SHALL return 0 and SHALL NOT call the provider (no
    fetch_ohlcv / ingest_candles invocation).

On the UNFIXED code this test MUST FAIL: _ingest_incremental() has no freshness
short-circuit, so even when the latest candle is still inside the in-progress bar
(H1, latest ts=09:30, now=10:00 → age 30min < 1 bar) it computes a `since`
window (watermark − 2 bars) and calls ingest_candles(), which invokes
provider.fetch_ohlcv(). The spy provider therefore records a call, reproducing
BUG 5 (wasted TwelveData credit + worsened scheduling burst).

DO NOT fix the code or this test when it fails — the failure is the success case
for this exploration step.

**Validates: Requirements 1.10, 2.10**
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.data.base import Candle, MarketDataProvider
from rtrade.persistence.repositories import CandleRow


class _SpyProvider(MarketDataProvider):
    """Spy provider that records every fetch_ohlcv invocation (Bug Condition C5)."""

    def __init__(self) -> None:
        self.fetch_calls: list[dict[str, Any]] = []

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        # Record the call — its presence is the BUG 5 counterexample.
        self.fetch_calls.append({"symbol": symbol, "since": since, "limit": limit})
        return []

    async def fetch_quote(self, symbol: str) -> Any:  # pragma: no cover - unused here
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - unused here
        pass


class _FakeRepo:
    """Repo returning a still-fresh latest candle; records upserts."""

    def __init__(self, latest_candle: Any) -> None:
        self._latest = latest_candle
        self.upserts: list[CandleRow] = []

    async def latest(self, instrument_id: int, tf: Timeframe) -> Any:
        return self._latest

    async def upsert_many(self, rows: list[CandleRow]) -> int:
        self.upserts.extend(rows)
        return len(rows)


class _FreshCandle:
    """Latest candle whose ts is well inside the in-progress H1 bar."""

    ts = datetime(2026, 6, 11, 9, 30, tzinfo=UTC)


def _make_instrument() -> InstrumentConfig:
    return InstrumentConfig(
        symbol="XAUUSD",
        market=Market.METALS,
        provider="twelvedata",
        provider_symbol="XAU/USD",
        timeframes=[Timeframe.H1],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


@pytest.mark.asyncio
async def test_fresh_watermark_returns_zero_without_calling_provider() -> None:
    """Fresh watermark (age < 1 bar) → return 0, no provider fetch (Property 6).

    Scoped to _ingest_incremental() with H1, latest ts=09:30, now=10:00
    (age = 30min < 1h bar). On the fixed code the function short-circuits and
    returns 0 without touching the provider; on the unfixed code it still fetches,
    so the spy records a fetch_ohlcv call and this test fails — confirming BUG 5.
    """
    from rtrade.pipeline.scan import _ingest_incremental

    provider = _SpyProvider()
    repo = _FakeRepo(latest_candle=_FreshCandle())
    now = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)

    count = await _ingest_incremental(
        provider,
        _make_instrument(),
        1,
        Timeframe.H1,
        repo,  # type: ignore[arg-type]
        now,
    )

    assert provider.fetch_calls == [], (
        "BUG 5 reproduced: _ingest_incremental() called the provider for a "
        f"still-fresh watermark (age < 1 bar); recorded fetch_ohlcv calls: "
        f"{provider.fetch_calls}."
    )
    assert count == 0, (
        "BUG 5 reproduced: _ingest_incremental() did not return 0 for a fresh "
        f"watermark; returned {count}."
    )
