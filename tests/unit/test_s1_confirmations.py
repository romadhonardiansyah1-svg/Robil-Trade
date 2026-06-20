# tests/unit/test_s1_confirmations.py
from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.strategies.base import StrategyConfig
from rtrade.strategies.s1_trend_pullback import S1TrendPullback


def _ohlc(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    close = pd.Series(closes, dtype="float64")
    df = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + spread,
            "low": close - spread,
            "close": close,
        }
    )
    # EMA columns required by populate_indicators.
    df["ema21"] = close
    df["ema50"] = close - 1.0
    df["ema200"] = close - 5.0
    df["adx"] = 30.0
    df["rsi"] = 50.0
    return df


def _downtrend() -> pd.DataFrame:
    return _ohlc([130.0 - i for i in range(40)])


def _uptrend() -> pd.DataFrame:
    return _ohlc([100.0 + i for i in range(40)])


def test_confirmations_default_off_returns_true() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()  # no confirmation attrs set
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_populate_indicators_defaults_all_disabled() -> None:
    s1 = S1TrendPullback()
    df = s1.populate_indicators(_uptrend(), StrategyConfig(raw={}))
    assert df.attrs["s1_st_enabled"] is False
    assert df.attrs["s1_adx_filter_enabled"] is False
    assert df.attrs["s1_chop_enabled"] is False
    assert df.attrs["s1_mtf_enabled"] is False


def test_supertrend_gate_blocks_buy_in_downtrend() -> None:
    s1 = S1TrendPullback()
    df = _downtrend()
    df.attrs["s1_st_enabled"] = True
    df.attrs["s1_st_period"] = 10
    df.attrs["s1_st_mult"] = 3.0
    assert s1._passes_confirmations(df, Action.BUY) is False


def test_supertrend_gate_allows_buy_in_uptrend() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_st_enabled"] = True
    df.attrs["s1_st_period"] = 10
    df.attrs["s1_st_mult"] = 3.0
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_adx_gate_blocks_when_below_threshold() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df["adx"] = 18.0
    df.attrs["s1_adx_filter_enabled"] = True
    df.attrs["s1_adx_threshold"] = 25.0
    assert s1._passes_confirmations(df, Action.BUY) is False


def test_mtf_bias_blocks_misaligned_and_allows_aligned() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_mtf_enabled"] = True
    df.attrs["s1_htf_bias"] = "DOWN"
    assert s1._passes_confirmations(df, Action.BUY) is False
    df.attrs["s1_htf_bias"] = "UP"
    assert s1._passes_confirmations(df, Action.BUY) is True


def test_mtf_bias_permissive_when_absent() -> None:
    s1 = S1TrendPullback()
    df = _uptrend()
    df.attrs["s1_mtf_enabled"] = True  # no s1_htf_bias injected
    assert s1._passes_confirmations(df, Action.BUY) is True
