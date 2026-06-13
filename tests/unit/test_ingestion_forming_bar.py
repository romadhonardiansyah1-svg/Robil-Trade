"""T5: Drop forming bars at ingestion — anti look-ahead tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from freezegun import freeze_time
import pytest

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.data.base import Candle, MarketDataProvider
from rtrade.data.ingestion import ingest_candles
from rtrade.persistence.repositories import CandleRow


class FakeProvider(MarketDataProvider):
    """Provider that returns pre-set candles."""

    def __init__(self, candles: list[Candle]) -> None:
        self._candles = candles

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        return self._candles

    async def fetch_quote(self, symbol: str) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


class FakeRepo:
    """Repo that records upserted rows."""

    def __init__(self) -> None:
        self.rows: list[CandleRow] = []

    async def upsert_many(self, rows: list[CandleRow]) -> int:
        self.rows.extend(rows)
        return len(rows)


def _make_candle(ts: datetime) -> Candle:
    return Candle(
        symbol="XAUUSD",
        ts=ts,
        timeframe=Timeframe.H1,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
    )


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


class TestFormingBarFilter:
    @freeze_time("2026-06-11 10:00:35", tz_offset=0)
    @pytest.mark.asyncio
    async def test_forming_bar_dropped(self) -> None:
        """Candle at 10:00 is forming (only 35s old); should be dropped."""
        candles = [
            _make_candle(datetime(2026, 6, 11, 8, 0, tzinfo=UTC)),
            _make_candle(datetime(2026, 6, 11, 9, 0, tzinfo=UTC)),
            _make_candle(datetime(2026, 6, 11, 10, 0, tzinfo=UTC)),  # forming
        ]
        provider = FakeProvider(candles)
        repo = FakeRepo()
        instrument = _make_instrument()

        count = await ingest_candles(
            provider,
            instrument,
            1,
            Timeframe.H1,
            repo,  # type: ignore[arg-type]
            since=datetime(2026, 6, 11, 7, 0, tzinfo=UTC),
        )

        assert count == 2
        timestamps = [r.ts for r in repo.rows]
        assert datetime(2026, 6, 11, 10, 0, tzinfo=UTC) not in timestamps
        assert datetime(2026, 6, 11, 8, 0, tzinfo=UTC) in timestamps
        assert datetime(2026, 6, 11, 9, 0, tzinfo=UTC) in timestamps
