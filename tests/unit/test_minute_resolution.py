"""T22: Tests for minute resolution of ambiguous bars."""

from rtrade.papertrack.minute_resolution import resolve_ambiguous_bar
from rtrade.papertrack.tracker import CandleBar


def test_tp_first_minute_resolution() -> None:
    """TP hit at minute 10, SL at minute 40 → TP_HIT."""
    candles = [
        CandleBar(high=101.0, low=99.5, close=100.5),  # no hit
        CandleBar(high=104.5, low=100.0, close=103.0),  # TP hit (high >= 104)
        CandleBar(high=100.0, low=97.5, close=98.0),  # SL hit (would be)
    ]
    result = resolve_ambiguous_bar("BUY", 100.0, 98.0, 104.0, candles)
    assert result == "TP"


def test_sl_first_minute_resolution() -> None:
    """SL hit before TP → SL."""
    candles = [
        CandleBar(high=100.5, low=97.5, close=98.0),  # SL hit (low <= 98)
        CandleBar(high=105.0, low=99.0, close=104.0),  # TP hit later
    ]
    result = resolve_ambiguous_bar("BUY", 100.0, 98.0, 104.0, candles)
    assert result == "SL"


def test_empty_candles_worst_case() -> None:
    """No minute data → worst case SL."""
    result = resolve_ambiguous_bar("BUY", 100.0, 98.0, 104.0, [])
    assert result == "SL"


def test_sell_tp_first() -> None:
    """SELL: TP hit first (low <= tp)."""
    candles = [
        CandleBar(high=101.0, low=96.0, close=97.0),  # TP hit (low <= 96)
    ]
    result = resolve_ambiguous_bar("SELL", 100.0, 102.0, 96.0, candles)
    assert result == "TP"
