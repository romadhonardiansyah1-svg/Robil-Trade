# tests/unit/test_filters.py
from __future__ import annotations

import numpy as np
import pandas as pd

from rtrade.strategies.filters import (
    adx_ok,
    bollinger_touch,
    choppiness_index,
    keltner_touch,
    rsi_divergence,
    supertrend,
    supertrend_flip,
)


def _ohlc(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    """Build an OHLC frame from a close path; high/low straddle close by `spread`."""
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + spread,
            "low": close - spread,
            "close": close,
        }
    )


def _down_then_up() -> pd.DataFrame:
    # 30 bars declining 130 -> 101 (step -1), then 15 bars rallying 103 -> 131 (step +2).
    down = [130.0 - i for i in range(30)]  # 130 .. 101
    up = [103.0 + 2.0 * i for i in range(15)]  # 103 .. 131
    return _ohlc(down + up)


def test_supertrend_direction_flips_down_to_up() -> None:
    df = _down_then_up()
    direction = supertrend(df, period=10, multiplier=3.0)
    assert int(direction.iloc[5]) == -1  # mid-downtrend
    assert int(direction.iloc[-1]) == 1  # after the rally
    assert set(direction.unique()) == {-1, 1}


def test_supertrend_flip_marks_the_reversal() -> None:
    df = _down_then_up()
    flips = supertrend_flip(df, period=10, multiplier=3.0)
    assert bool(flips.iloc[0]) is False  # first bar can never be a flip
    assert int(flips.iloc[1:].sum()) >= 1  # at least one -1 -> +1 reversal


def test_supertrend_steady_uptrend_is_all_plus_one_after_warmup() -> None:
    df = _ohlc([100.0 + i for i in range(40)])
    direction = supertrend(df, period=10, multiplier=3.0)
    assert int(direction.iloc[-1]) == 1
    assert not np.isnan(float(direction.iloc[-1]))


def test_choppiness_low_in_clean_trend() -> None:
    # Monotonic ramp = clean trend -> low CI.
    df = _ohlc([100.0 + i for i in range(50)])
    ci = choppiness_index(df, period=14)
    assert float(ci.iloc[-1]) < 38.2


def test_choppiness_high_in_oscillation() -> None:
    # Price ping-pongs 100/102 -> lots of travel, tiny net range -> high CI.
    closes = [100.0 if i % 2 == 0 else 102.0 for i in range(50)]
    df = _ohlc(closes)
    ci = choppiness_index(df, period=14)
    assert float(ci.iloc[-1]) > 61.8


def test_choppiness_warmup_is_nan() -> None:
    df = _ohlc([100.0 + i for i in range(50)])
    ci = choppiness_index(df, period=14)
    assert np.isnan(float(ci.iloc[5]))


def _adx_df(value: float) -> pd.DataFrame:
    df = _ohlc([100.0, 101.0, 102.0])
    df["adx"] = [10.0, 20.0, value]
    return df


def test_adx_ok_true_above_threshold() -> None:
    assert adx_ok(_adx_df(30.0), threshold=25.0) is True


def test_adx_ok_false_below_threshold() -> None:
    assert adx_ok(_adx_df(20.0), threshold=25.0) is False


def test_adx_ok_false_when_column_missing() -> None:
    assert adx_ok(_ohlc([100.0, 101.0]), threshold=25.0) is False


def test_adx_ok_false_when_nan() -> None:
    assert adx_ok(_adx_df(float("nan")), threshold=25.0) is False


def _bb_frame() -> pd.DataFrame:
    # 20 closes alternating 95/105 -> mean=100, population std (ddof=0)=5 exactly.
    # With std mult 2.0 -> lower=90, upper=110.
    closes = [95.0 if i % 2 == 0 else 105.0 for i in range(20)]
    close = pd.Series(closes, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
        }
    )


def test_bollinger_touch_lower_true() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 89.0  # below lower band (90)
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is True


def test_bollinger_touch_lower_false() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 95.0  # above lower band (90)
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is False


def test_bollinger_touch_upper_true() -> None:
    df = _bb_frame()
    df.iloc[-1, df.columns.get_loc("high")] = 111.0  # above upper band (110)
    assert bollinger_touch(df, period=20, std=2.0, side="upper") is True


def test_bollinger_touch_too_short_false() -> None:
    df = _ohlc([100.0, 101.0, 102.0])
    assert bollinger_touch(df, period=20, std=2.0, side="lower") is False


def _kc_frame() -> pd.DataFrame:
    # close flat at 100, high/low ±2 -> TR=4 every bar -> ATR=4, EMA=100.
    # mult 1.5 -> lower=94, upper=106.
    close = pd.Series([100.0] * 20, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
        }
    )


def test_keltner_touch_lower_true() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 93.0  # below lower (94)
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is True


def test_keltner_touch_lower_false() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("low")] = 96.0  # above lower (94)
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is False


def test_keltner_touch_upper_true() -> None:
    df = _kc_frame()
    df.iloc[-1, df.columns.get_loc("high")] = 107.0  # above upper (106)
    assert keltner_touch(df, period=20, multiplier=1.5, side="upper") is True


def test_keltner_touch_too_short_false() -> None:
    df = _ohlc([100.0, 101.0])
    assert keltner_touch(df, period=20, multiplier=1.5, side="lower") is False


def _div_frame(lows: list[float], highs: list[float], rsis: list[float]) -> pd.DataFrame:
    n = len(lows)
    close = pd.Series([100.0] * n, dtype="float64")
    return pd.DataFrame(
        {
            "open": close,
            "high": pd.Series(highs, dtype="float64"),
            "low": pd.Series(lows, dtype="float64"),
            "close": close,
            "rsi": pd.Series(rsis, dtype="float64"),
        }
    )


def test_rsi_bullish_divergence() -> None:
    # older-half min low = 100 @ idx5 (rsi 30); recent-half min low = 98 @ idx15 (rsi 35).
    lows = [105.0] * 20
    lows[5] = 100.0
    lows[15] = 98.0  # lower low
    highs = [110.0] * 20
    rsis = [50.0] * 20
    rsis[5] = 30.0
    rsis[15] = 35.0  # higher low in RSI
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "bullish"


def test_rsi_bearish_divergence() -> None:
    # older-half max high = 110 @ idx5 (rsi 70); recent-half max high = 112 @ idx15 (rsi 65).
    highs = [105.0] * 20
    highs[5] = 110.0
    highs[15] = 112.0  # higher high
    lows = [100.0] * 20
    rsis = [50.0] * 20
    rsis[5] = 70.0
    rsis[15] = 65.0  # lower high in RSI
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "bearish"


def test_rsi_no_divergence_when_flat() -> None:
    lows = [100.0] * 20
    highs = [110.0] * 20
    rsis = [50.0] * 20
    assert rsi_divergence(_div_frame(lows, highs, rsis), lookback=20) == "none"


def test_rsi_divergence_missing_column_is_none() -> None:
    df = _ohlc([100.0 + i for i in range(20)])
    assert rsi_divergence(df, lookback=20) == "none"
