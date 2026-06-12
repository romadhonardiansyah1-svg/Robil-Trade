"""W1: Tests for track_paper_signals helpers."""

from datetime import UTC, datetime

from rtrade.papertrack.tracker import CandleBar
from rtrade.pipeline.scan import _bar_is_ambiguous, _first_touch_index


def test_bar_is_ambiguous_buy_true() -> None:
    """BUY: bar where low<SL AND high>TP → ambiguous."""
    ts = datetime(2025, 1, 1, 12, tzinfo=UTC)
    bars = [CandleBar(high=105.0, low=97.0, close=100.0, ts=ts)]
    assert _bar_is_ambiguous("BUY", 98.0, 104.0, bars, ts) is True


def test_bar_is_ambiguous_buy_false() -> None:
    """BUY: bar only hits SL, not TP → not ambiguous."""
    ts = datetime(2025, 1, 1, 12, tzinfo=UTC)
    bars = [CandleBar(high=102.0, low=97.0, close=99.0, ts=ts)]
    assert _bar_is_ambiguous("BUY", 98.0, 104.0, bars, ts) is False


def test_bar_is_ambiguous_no_match_ts() -> None:
    """No bar matches resolved_at → False."""
    ts = datetime(2025, 1, 1, 12, tzinfo=UTC)
    other_ts = datetime(2025, 1, 1, 13, tzinfo=UTC)
    bars = [CandleBar(high=105.0, low=97.0, close=100.0, ts=ts)]
    assert _bar_is_ambiguous("BUY", 98.0, 104.0, bars, other_ts) is False


def test_first_touch_index_found() -> None:
    """Entry touched at bar index 1."""
    bars = [
        CandleBar(high=99.0, low=97.0, close=98.0),
        CandleBar(high=101.0, low=99.5, close=100.5),  # touches 100
        CandleBar(high=102.0, low=100.5, close=101.0),
    ]
    assert _first_touch_index("BUY", 100.0, bars) == 1


def test_first_touch_index_not_found() -> None:
    """Entry never touched → None."""
    bars = [
        CandleBar(high=99.0, low=97.0, close=98.0),
        CandleBar(high=99.5, low=97.5, close=98.5),
    ]
    assert _first_touch_index("BUY", 100.0, bars) is None
