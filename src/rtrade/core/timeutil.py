"""Time utilities — the single source of truth for candle-time math.

Rules (IMPLEMENTATION_PLAN §0.6, §0.7):
- Every datetime is timezone-aware; naive datetimes raise DataValidationError.
- All math is done in UTC. Candle boundaries are epoch-aligned, so 4h candles
  open at 00/04/08/12/16/20 UTC and 1d candles at 00:00 UTC — immune to DST.
- A candle is identified by its OPEN time (matches the `candles.ts` column).
- Convention: at exactly t == boundary, the candle closing at t is considered
  closed (the scheduler additionally waits a safety buffer after close).
"""

from datetime import UTC, datetime, timedelta

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError

_TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}


def timeframe_duration(timeframe: Timeframe) -> timedelta:
    """Duration of one candle of `timeframe`."""
    return _TIMEFRAME_DURATION[timeframe]


def utcnow() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Return `ts` converted to UTC; reject naive datetimes loudly."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise DataValidationError(
            "naive datetime rejected: all timestamps must be timezone-aware (UTC)"
        )
    return ts.astimezone(UTC)


def floor_to_candle(ts: datetime, timeframe: Timeframe) -> datetime:
    """Open time of the candle that contains `ts` (epoch-aligned, UTC)."""
    ts = ensure_utc(ts)
    period = int(timeframe_duration(timeframe).total_seconds())
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % period), tz=UTC)


def candle_close_time(open_ts: datetime, timeframe: Timeframe) -> datetime:
    """Close time of the candle that opened at `open_ts`."""
    return ensure_utc(open_ts) + timeframe_duration(timeframe)


def last_closed_candle_open(timeframe: Timeframe, now: datetime | None = None) -> datetime:
    """Open time of the most recent candle that has ALREADY CLOSED.

    The candle currently forming opened at floor(now); the last closed one is
    exactly one period earlier. Strategies may only ever see candles up to and
    including this one (anti look-ahead, PLAN §0.7).
    """
    now = ensure_utc(now) if now is not None else utcnow()
    return floor_to_candle(now, timeframe) - timeframe_duration(timeframe)


def next_candle_close(timeframe: Timeframe, now: datetime | None = None) -> datetime:
    """Close time of the candle currently forming (= the next scheduler tick base)."""
    now = ensure_utc(now) if now is not None else utcnow()
    return floor_to_candle(now, timeframe) + timeframe_duration(timeframe)


def is_candle_fresh(
    last_open_ts: datetime,
    timeframe: Timeframe,
    *,
    staleness_factor: float = 2.0,
    now: datetime | None = None,
) -> bool:
    """GR-06 freshness check.

    A dataset whose latest candle OPENED at `last_open_ts` is fresh when
    `now - close_time <= staleness_factor * timeframe`. With the default
    factor 2 on 1h data: at most 2h after the latest close.
    """
    now = ensure_utc(now) if now is not None else utcnow()
    close = candle_close_time(last_open_ts, timeframe)
    return (now - close) <= staleness_factor * timeframe_duration(timeframe)
