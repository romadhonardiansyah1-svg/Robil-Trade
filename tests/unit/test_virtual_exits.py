"""T23: Tests for virtual exit ensemble."""

from rtrade.papertrack.tracker import CandleBar
from rtrade.papertrack.virtual_exits import evaluate_virtual_exits


def test_fixed_sl_hit() -> None:
    """BUY: price goes up 1R then drops to SL."""
    candles = [
        CandleBar(high=102.0, low=99.5, close=101.0),  # +1R
        CandleBar(high=100.5, low=97.5, close=98.0),  # SL hit
    ]
    r = evaluate_virtual_exits("BUY", 100.0, 98.0, 104.0, 0.5, candles)
    assert r["fixed_2r"]["status"] == "SL_HIT"
    assert r["fixed_2r"]["outcome_r"] == -1.0


def test_fixed_tp_hit() -> None:
    """BUY: straight to TP."""
    candles = [
        CandleBar(high=104.5, low=100.0, close=104.0),  # TP hit
    ]
    r = evaluate_virtual_exits("BUY", 100.0, 98.0, 104.0, 0.5, candles)
    assert r["fixed_2r"]["status"] == "TP_HIT"
    assert r["fixed_2r"]["outcome_r"] == 2.0


def test_partial_be_then_sl() -> None:
    """BUY: goes to 1R (partial) then back to entry (BE SL)."""
    candles = [
        CandleBar(high=102.5, low=100.5, close=102.0),  # +1R → partial
        CandleBar(high=101.0, low=99.5, close=100.0),  # BE SL hit (low <= 100)
    ]
    r = evaluate_virtual_exits("BUY", 100.0, 98.0, 104.0, 0.5, candles)
    # partial: 0.5 * 1.0 = 0.5R, BE exit at 100: 0.5 * 0R = 0
    assert r["partial_be"]["outcome_r"] == 0.5


def test_time_stop_12_exit() -> None:
    """BUY: 12 bars of flat → time exit."""
    candles = [CandleBar(high=100.5, low=99.5, close=100.2) for _ in range(12)]
    r = evaluate_virtual_exits("BUY", 100.0, 98.0, 104.0, 0.5, candles)
    assert r["time_stop_12"]["status"] == "TIME_EXIT"
    assert r["time_stop_12"]["outcome_r"] is not None
