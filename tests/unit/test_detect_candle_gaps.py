"""D4: timeframe-aware candle-gap detection.

The weekend/holiday suppression threshold must be derived from the timeframe
duration instead of a hardcoded `missing_count <= 72` (which assumed H1):
- D1: a real multi-week gap must be FLAGGED (not suppressed as a "weekend").
- M5/M15: a normal weekend must NOT be over-flagged.
- H1: a ~3-day weekend must still be suppressed (regression).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from rtrade.core.constants import Timeframe
from rtrade.data.base import Candle
from rtrade.data.ingestion import detect_candle_gaps


def _candle(ts: datetime, tf: Timeframe) -> Candle:
    return Candle(
        symbol="XAUUSD",
        ts=ts,
        timeframe=tf,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=Decimal("1000"),
    )


def test_d1_multiweek_gap_is_flagged() -> None:
    """A 10-day D1 gap starting on a Friday must be flagged, not suppressed.

    2026-01-02 is a Friday; the old `<= 72` check (assuming H1) treated the
    10 missing daily bars as a weekend and hid a real multi-week outage.
    """
    candles = [
        _candle(datetime(2026, 1, 1, tzinfo=UTC), Timeframe.D1),  # Thursday
        _candle(datetime(2026, 1, 2, tzinfo=UTC), Timeframe.D1),  # Friday
        _candle(datetime(2026, 1, 13, tzinfo=UTC), Timeframe.D1),  # +10 days
    ]
    gaps = detect_candle_gaps(candles, Timeframe.D1)
    assert len(gaps) == 1
    assert gaps[0][0] == datetime(2026, 1, 2, tzinfo=UTC)
    assert gaps[0][1] == datetime(2026, 1, 13, tzinfo=UTC)


def test_m5_normal_weekend_not_overflagged() -> None:
    """A normal M5 weekend (Fri close → Mon open) must NOT be flagged.

    The old `<= 72` check spammed ~600 missing 5-minute bars as a gap.
    """
    candles = [
        _candle(datetime(2026, 1, 2, 20, 55, tzinfo=UTC), Timeframe.M5),  # Fri close
        _candle(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), Timeframe.M5),  # Mon open
    ]
    gaps = detect_candle_gaps(candles, Timeframe.M5)
    assert gaps == []


def test_h1_weekend_still_suppressed() -> None:
    """Regression: a ~3-day H1 weekend remains suppressed."""
    candles = [
        _candle(datetime(2026, 1, 2, 20, 0, tzinfo=UTC), Timeframe.H1),  # Fri
        _candle(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), Timeframe.H1),  # Mon
    ]
    gaps = detect_candle_gaps(candles, Timeframe.H1)
    assert gaps == []


def test_h1_midweek_gap_still_flagged() -> None:
    """Regression: a genuine mid-week H1 outage is still flagged."""
    candles = [
        _candle(datetime(2026, 1, 7, 10, 0, tzinfo=UTC), Timeframe.H1),  # Wed
        _candle(datetime(2026, 1, 7, 20, 0, tzinfo=UTC), Timeframe.H1),  # +10h
    ]
    gaps = detect_candle_gaps(candles, Timeframe.H1)
    assert len(gaps) == 1


def test_crypto_gap_always_flagged() -> None:
    """Crypto trades 24/7: any gap beyond the threshold is flagged."""
    candles = [
        _candle(datetime(2026, 1, 2, 20, 0, tzinfo=UTC), Timeframe.H1),
        _candle(datetime(2026, 1, 5, 0, 0, tzinfo=UTC), Timeframe.H1),
    ]
    gaps = detect_candle_gaps(candles, Timeframe.H1, is_crypto=True)
    assert len(gaps) == 1
