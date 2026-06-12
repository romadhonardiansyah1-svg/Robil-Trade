"""T24: Tests for MAE/MFE excursion capture."""

import pytest

from rtrade.papertrack.excursion import compute_excursion
from rtrade.papertrack.tracker import CandleBar


def test_buy_excursion() -> None:
    """BUY entry 100, SL 98: path 99→103→TP."""
    candles = [
        CandleBar(high=100.5, low=99.0, close=100.0),  # dip to 99
        CandleBar(high=103.0, low=100.5, close=102.5),  # rally to 103
        CandleBar(high=104.0, low=102.0, close=104.0),  # TP
    ]
    mae, mfe = compute_excursion("BUY", 100.0, 98.0, candles)
    assert mae is not None and mfe is not None
    assert mae == pytest.approx(-0.5, abs=0.01)  # (99-100)/2 = -0.5R
    assert mfe == pytest.approx(2.0, abs=0.01)  # (104-100)/2 = 2.0R


def test_sell_excursion() -> None:
    """SELL entry 100, SL 102: price drops."""
    candles = [
        CandleBar(high=100.5, low=98.0, close=99.0),  # favorable
    ]
    mae, mfe = compute_excursion("SELL", 100.0, 102.0, candles)
    assert mae is not None and mfe is not None
    assert mae == pytest.approx(-0.25, abs=0.01)  # (100-100.5)/2 = -0.25R
    assert mfe == pytest.approx(1.0, abs=0.01)  # (100-98)/2 = 1.0R


def test_no_candles() -> None:
    mae, mfe = compute_excursion("BUY", 100.0, 98.0, [])
    assert mae is None
    assert mfe is None
