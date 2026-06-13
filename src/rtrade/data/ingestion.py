"""Data ingestion job — fetch → validate → upsert DB (PLAN §8.1).

The ingestion module bridges providers and persistence. It:
1. Calls the appropriate MarketDataProvider based on instrument config.
2. Validates candle data (OHLC consistency, timestamps, gap detection).
3. Upserts into the candles table via CandleRepo.
4. Detects data gaps (>3 consecutive missing candles) and logs warnings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError
from rtrade.core.timeutil import ensure_utc, last_closed_candle_open, timeframe_duration
from rtrade.data.base import Candle, MarketDataProvider
from rtrade.persistence.repositories import CandleRow

if TYPE_CHECKING:
    from rtrade.persistence.repositories import CandleRepo

logger = structlog.get_logger(__name__)


def candle_to_row(candle: Candle, instrument_id: int) -> CandleRow:
    """Convert domain Candle to persistence CandleRow."""
    return CandleRow(
        instrument_id=instrument_id,
        timeframe=candle.timeframe.value,
        ts=candle.ts,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def detect_gaps(
    candles: list[Candle],
    timeframe: Timeframe,
    *,
    is_crypto: bool = False,
    max_consecutive_gaps: int = 3,
) -> list[tuple[datetime, datetime]]:
    """Detect gaps in candle data.

    For non-crypto instruments, weekend gaps are expected and excluded.
    Returns list of (gap_start, gap_end) tuples.
    """
    if len(candles) < 2:
        return []

    td = timeframe_duration(timeframe)
    gaps: list[tuple[datetime, datetime]] = []

    for i in range(1, len(candles)):
        expected_ts = candles[i - 1].ts + td
        actual_ts = candles[i].ts

        if actual_ts <= expected_ts:
            continue

        # Count how many candles are missing.
        missing_count = int((actual_ts - expected_ts) / td)

        if not is_crypto:
            # Weekend gap: Friday → Monday is normal for FX/metals.
            prev_weekday = candles[i - 1].ts.weekday()
            curr_weekday = actual_ts.weekday()
            # Friday (4) close → Monday (0) open is expected.
            if prev_weekday == 4 and curr_weekday == 0:
                continue
            # Also handle holiday gaps spanning 1-2 extra days.
            if missing_count <= 72 and prev_weekday >= 4:  # up to 3 days off
                continue

        if missing_count > max_consecutive_gaps:
            gaps.append((candles[i - 1].ts, actual_ts))

    return gaps


async def ingest_candles(
    provider: MarketDataProvider,
    instrument: InstrumentConfig,
    instrument_id: int,
    timeframe: Timeframe,
    repo: CandleRepo,
    *,
    since: datetime | None = None,
    limit: int = 500,
) -> int:
    """Fetch, validate, and upsert candles for one instrument × timeframe.

    Returns the number of candles upserted.
    Raises ProviderError on fetch failure (caller should handle retry/skip).
    """
    if since is None:
        # Default: fetch from 2 days ago.
        since = datetime.now(UTC) - timedelta(days=2)
    since = ensure_utc(since)

    logger.info(
        "ingesting candles",
        symbol=instrument.symbol,
        timeframe=timeframe.value,
        since=since.isoformat(),
    )

    try:
        candles = await provider.fetch_ohlcv(
            instrument.provider_symbol,
            timeframe,
            since=since,
            limit=limit,
        )
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(
            f"unexpected error fetching {instrument.symbol} {timeframe.value}: {exc}"
        ) from exc

    if not candles:
        logger.warning("no candles returned", symbol=instrument.symbol, timeframe=timeframe.value)
        return 0

    # Drop forming (unclosed) bars — anti look-ahead (T5).
    cutoff = last_closed_candle_open(timeframe)
    closed_candles = [c for c in candles if c.ts <= cutoff]
    if len(closed_candles) < len(candles):
        logger.info(
            "dropped forming bars",
            symbol=instrument.symbol,
            timeframe=timeframe.value,
            dropped=len(candles) - len(closed_candles),
        )
    candles = closed_candles
    if not candles:
        return 0

    # Detect gaps.
    is_crypto = instrument.market == "crypto"
    gaps = detect_gaps(candles, timeframe, is_crypto=is_crypto)
    for gap_start, gap_end in gaps:
        logger.warning(
            "data gap detected",
            symbol=instrument.symbol,
            timeframe=timeframe.value,
            gap_start=gap_start.isoformat(),
            gap_end=gap_end.isoformat(),
        )

    # Convert to persistence rows and upsert.
    rows = [candle_to_row(c, instrument_id) for c in candles]
    upserted = await repo.upsert_many(rows)

    logger.info(
        "candles upserted",
        symbol=instrument.symbol,
        timeframe=timeframe.value,
        count=upserted,
        gaps=len(gaps),
    )
    return upserted
