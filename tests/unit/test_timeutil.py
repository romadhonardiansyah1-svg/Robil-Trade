"""Candle-time math is foundation for everything — exhaustive edge cases here."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError
from rtrade.core.timeutil import (
    candle_close_time,
    ensure_utc,
    floor_to_candle,
    is_candle_fresh,
    last_closed_candle_open,
    next_candle_close,
    timeframe_duration,
)

T = datetime(2026, 6, 11, 14, 30, 17, 123456, tzinfo=UTC)  # mid-candle reference


class TestEnsureUtc:
    def test_naive_rejected(self) -> None:
        with pytest.raises(DataValidationError, match="naive datetime"):
            ensure_utc(datetime(2026, 6, 11, 14, 30))  # noqa: DTZ001 — intentional naive

    def test_non_utc_converted(self) -> None:
        jakarta = timezone(timedelta(hours=7))
        ts = datetime(2026, 6, 11, 21, 30, tzinfo=jakarta)
        assert ensure_utc(ts) == datetime(2026, 6, 11, 14, 30, tzinfo=UTC)

    def test_utc_passthrough(self) -> None:
        assert ensure_utc(T) == T


class TestFloorToCandle:
    @pytest.mark.parametrize(
        ("tf", "expected"),
        [
            (Timeframe.H1, datetime(2026, 6, 11, 14, 0, tzinfo=UTC)),
            (Timeframe.H4, datetime(2026, 6, 11, 12, 0, tzinfo=UTC)),
            (Timeframe.D1, datetime(2026, 6, 11, 0, 0, tzinfo=UTC)),
        ],
    )
    def test_mid_candle(self, tf: Timeframe, expected: datetime) -> None:
        assert floor_to_candle(T, tf) == expected

    def test_exact_boundary_is_identity(self) -> None:
        boundary = datetime(2026, 6, 11, 16, 0, tzinfo=UTC)
        for tf in (Timeframe.H1, Timeframe.H4):
            assert floor_to_candle(boundary, tf) == boundary

    def test_h4_epoch_alignment(self) -> None:
        # 4h candles must open at 00/04/08/12/16/20 UTC.
        ts = datetime(2026, 6, 11, 3, 59, 59, tzinfo=UTC)
        assert floor_to_candle(ts, Timeframe.H4) == datetime(2026, 6, 11, 0, 0, tzinfo=UTC)

    def test_non_utc_input(self) -> None:
        jakarta = timezone(timedelta(hours=7))
        ts = datetime(2026, 6, 11, 21, 30, tzinfo=jakarta)  # == 14:30 UTC
        assert floor_to_candle(ts, Timeframe.H1) == datetime(2026, 6, 11, 14, 0, tzinfo=UTC)


class TestCandleLifecycle:
    def test_close_time(self) -> None:
        open_ts = datetime(2026, 6, 11, 14, 0, tzinfo=UTC)
        assert candle_close_time(open_ts, Timeframe.H1) == datetime(2026, 6, 11, 15, 0, tzinfo=UTC)

    def test_last_closed_candle_open_mid_candle(self) -> None:
        # At 14:30 the 14:00 candle is still forming; last CLOSED one opened 13:00.
        assert last_closed_candle_open(Timeframe.H1, now=T) == datetime(
            2026, 6, 11, 13, 0, tzinfo=UTC
        )

    def test_last_closed_candle_open_exact_boundary(self) -> None:
        # At exactly 15:00, the 14:00 candle has just closed (convention).
        boundary = datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
        assert last_closed_candle_open(Timeframe.H1, now=boundary) == datetime(
            2026, 6, 11, 14, 0, tzinfo=UTC
        )

    def test_next_candle_close(self) -> None:
        assert next_candle_close(Timeframe.H1, now=T) == datetime(2026, 6, 11, 15, 0, tzinfo=UTC)
        assert next_candle_close(Timeframe.H4, now=T) == datetime(2026, 6, 11, 16, 0, tzinfo=UTC)


class TestFreshness:
    OPEN = datetime(2026, 6, 11, 13, 0, tzinfo=UTC)  # closed at 14:00

    def test_fresh_within_factor(self) -> None:
        now = datetime(2026, 6, 11, 15, 30, tzinfo=UTC)  # 1.5h after close
        assert is_candle_fresh(self.OPEN, Timeframe.H1, staleness_factor=2.0, now=now)

    def test_fresh_at_exact_boundary(self) -> None:
        now = datetime(2026, 6, 11, 16, 0, tzinfo=UTC)  # exactly 2h after close
        assert is_candle_fresh(self.OPEN, Timeframe.H1, staleness_factor=2.0, now=now)

    def test_stale_beyond_factor(self) -> None:
        now = datetime(2026, 6, 11, 16, 0, 1, tzinfo=UTC)
        assert not is_candle_fresh(self.OPEN, Timeframe.H1, staleness_factor=2.0, now=now)


def test_timeframe_durations() -> None:
    assert timeframe_duration(Timeframe.H1) == timedelta(hours=1)
    assert timeframe_duration(Timeframe.H4) == timedelta(hours=4)
    assert timeframe_duration(Timeframe.D1) == timedelta(days=1)
