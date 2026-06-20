from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.pipeline.mtf import aligned, h4_trend_bias


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="4h", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_rising_series_is_up() -> None:
    df = _frame([100.0 + i for i in range(80)])
    assert h4_trend_bias(df) == "UP"


def test_falling_series_is_down() -> None:
    df = _frame([300.0 - i for i in range(80)])
    assert h4_trend_bias(df) == "DOWN"


def test_flat_series_is_none() -> None:
    df = _frame([200.0] * 80)
    assert h4_trend_bias(df) == "NONE"


def test_insufficient_bars_is_none() -> None:
    df = _frame([100.0 + i for i in range(40)])  # < _MIN_BARS
    assert h4_trend_bias(df) == "NONE"


def test_empty_or_missing_close_is_none() -> None:
    assert h4_trend_bias(pd.DataFrame()) == "NONE"
    assert h4_trend_bias(pd.DataFrame({"open": [1.0, 2.0]})) == "NONE"


def test_aligned_truth_table() -> None:
    assert aligned("UP", Action.BUY) is True
    assert aligned("UP", Action.SELL) is False
    assert aligned("DOWN", Action.SELL) is True
    assert aligned("DOWN", Action.BUY) is False
    assert aligned("NONE", Action.BUY) is False
    assert aligned("NONE", Action.SELL) is False
    assert aligned("UP", Action.ABSTAIN) is False
