"""T6: Incremental ingestion with watermark tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.data.base import Candle, MarketDataProvider
from rtrade.persistence.repositories import CandleRow


class FakeRepo:
    """Repo that records calls to latest() and upsert_many()."""

    def __init__(self, latest_candle: Any = None) -> None:
        self._latest = latest_candle
        self.upserts: list[CandleRow] = []

    async def latest(self, instrument_id: int, tf: Timeframe) -> Any:
        return self._latest

    async def upsert_many(self, rows: list[CandleRow]) -> int:
        self.upserts.extend(rows)
        return len(rows)


class FakeProvider(MarketDataProvider):
    """Provider that records call arguments."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        self.calls.append({"since": since, "limit": limit})
        return []

    async def fetch_quote(self, symbol: str) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass


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


class TestIngestIncremental:
    @pytest.mark.asyncio
    async def test_first_run_backfills_120d(self) -> None:
        from rtrade.pipeline.scan import _ingest_incremental

        provider = FakeProvider()
        repo = FakeRepo(latest_candle=None)
        now = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)

        await _ingest_incremental(
            provider,
            _make_instrument(),
            1,
            Timeframe.H1,
            repo,
            now,  # type: ignore[arg-type]
        )

        assert len(provider.calls) == 1
        call = provider.calls[0]
        # P1-7 (FR-DATA-09): cold start backfills a full warmup window in one call.
        assert call["limit"] == 5000
        delta = abs((now - timedelta(days=120)) - call["since"])
        assert delta < timedelta(minutes=1)

    @pytest.mark.asyncio
    async def test_incremental_uses_watermark(self) -> None:
        from rtrade.pipeline.scan import _ingest_incremental

        class FakeCandle:
            ts = datetime(2026, 6, 11, 8, 0, tzinfo=UTC)

        provider = FakeProvider()
        repo = FakeRepo(latest_candle=FakeCandle())
        now = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)

        await _ingest_incremental(
            provider,
            _make_instrument(),
            1,
            Timeframe.H1,
            repo,
            now,  # type: ignore[arg-type]
        )

        assert len(provider.calls) == 1
        call = provider.calls[0]
        assert call["limit"] == 10
        # watermark - 2 bars = 08:00 - 2h = 06:00
        assert call["since"] == datetime(2026, 6, 11, 6, 0, tzinfo=UTC)

    @pytest.mark.asyncio
    async def test_fresh_latest_candle_skips_provider_fetch(self) -> None:
        from rtrade.pipeline.scan import _ingest_incremental

        class FakeCandle:
            ts = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)

        provider = FakeProvider()
        repo = FakeRepo(latest_candle=FakeCandle())
        now = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)

        count = await _ingest_incremental(
            provider,
            _make_instrument(),
            1,
            Timeframe.H1,
            repo,
            now,  # type: ignore[arg-type]
        )

        assert count == 0
        assert provider.calls == []
