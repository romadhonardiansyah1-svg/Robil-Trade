"""Unit tests for backfill pagination cursor math (E4).

The backfill loop must advance its cursor by exactly the timeframe duration
times the batch size — not a hardcoded H1/H4 delta. Otherwise D1 re-fetches
overlapping windows and M5/M15 skip data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rtrade.cli.backfill import _advance_cursor
from rtrade.core.constants import Timeframe
from rtrade.core.timeutil import timeframe_duration


@pytest.mark.parametrize(
    "tf",
    [Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1],
)
def test_advance_cursor_uses_timeframe_duration(tf: Timeframe) -> None:
    since = datetime(2025, 1, 1, tzinfo=UTC)
    out = _advance_cursor(since, tf)
    assert out == since + timeframe_duration(tf) * 499


def test_advance_cursor_d1_advances_499_days() -> None:
    since = datetime(2025, 1, 1, tzinfo=UTC)
    out = _advance_cursor(since, Timeframe.D1)
    assert (out - since).days == 499


def test_advance_cursor_m5_advances_499_times_5min() -> None:
    since = datetime(2025, 1, 1, tzinfo=UTC)
    out = _advance_cursor(since, Timeframe.M5)
    assert (out - since).total_seconds() == 499 * 5 * 60


def test_advance_cursor_h4_advances_499_times_4h() -> None:
    since = datetime(2025, 1, 1, tzinfo=UTC)
    out = _advance_cursor(since, Timeframe.H4)
    assert (out - since).total_seconds() == 499 * 4 * 3600


def test_advance_cursor_respects_batch_argument() -> None:
    since = datetime(2025, 1, 1, tzinfo=UTC)
    out = _advance_cursor(since, Timeframe.H1, batch=500)
    assert out == since + timeframe_duration(Timeframe.H1) * 500
