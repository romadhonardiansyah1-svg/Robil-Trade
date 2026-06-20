# tests/unit/test_s2_confirmations.py
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.strategies.base import StrategyConfig
from rtrade.strategies.s2_range_mr import S2RangeMR


def _bb_frame() -> pd.DataFrame:
    # mean=100, population std=5 -> BB(20,2) lower=90 upper=110.
    closes = [95.0 if i % 2 == 0 else 105.0 for i in range(20)]
    close = pd.Series(closes, dtype="float64")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "rsi": pd.Series([50.0] * 20, dtype="float64"),
        }
    )
    return df


def test_confirmations_default_off_returns_true() -> None:
    s2 = S2RangeMR()
    assert s2._passes_confirmations(_bb_frame(), Action.BUY) is True


def test_populate_indicators_defaults_all_disabled() -> None:
    s2 = S2RangeMR()
    df = s2.populate_indicators(_bb_frame(), StrategyConfig(raw={"range": {"band_lookback": 20}}))
    assert df.attrs["s2_bb_enabled"] is False
    assert df.attrs["s2_kc_enabled"] is False
    assert df.attrs["s2_rsidiv_enabled"] is False
    assert df.attrs["s2_chop_enabled"] is False


def test_bollinger_gate_blocks_without_touch() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 95.0  # above lower band (90) -> no touch
    df.attrs["s2_bb_enabled"] = True
    df.attrs["s2_bb_period"] = 20
    df.attrs["s2_bb_std"] = 2.0
    assert s2._passes_confirmations(df, Action.BUY) is False


def test_bollinger_gate_allows_on_touch() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 89.0  # below lower band (90) -> touch
    df.attrs["s2_bb_enabled"] = True
    df.attrs["s2_bb_period"] = 20
    df.attrs["s2_bb_std"] = 2.0
    assert s2._passes_confirmations(df, Action.BUY) is True


def test_rsi_divergence_gate_requires_bullish_for_buy() -> None:
    s2 = S2RangeMR()
    df = _bb_frame()
    # Build a bullish divergence: recent lower low @ higher RSI.
    df["low"] = [105.0] * 20
    df.iloc[5, df.columns.get_loc("low")] = 100.0
    df.iloc[15, df.columns.get_loc("low")] = 98.0
    df["rsi"] = [50.0] * 20
    df.iloc[5, df.columns.get_loc("rsi")] = 30.0
    df.iloc[15, df.columns.get_loc("rsi")] = 35.0
    df.attrs["s2_rsidiv_enabled"] = True
    df.attrs["s2_rsidiv_lookback"] = 20
    assert s2._passes_confirmations(df, Action.BUY) is True
    # A SELL would need bearish divergence -> blocked here.
    assert s2._passes_confirmations(df, Action.SELL) is False


def test_choppiness_gate_requires_range() -> None:
    s2 = S2RangeMR()
    # Clean trend -> low CI -> below choppiness_min -> blocked (not a real range).
    close = pd.Series([100.0 + i for i in range(50)], dtype="float64")
    df = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "rsi": pd.Series([50.0] * 50, dtype="float64"),
        }
    )
    df.attrs["s2_chop_enabled"] = True
    df.attrs["s2_chop_period"] = 14
    df.attrs["s2_chop_min"] = 61.8
    assert s2._passes_confirmations(df, Action.BUY) is False
