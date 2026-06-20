"""Pure confirmation predicates for swing strategies (SP-5).

Every function here is deterministic, side-effect free, and operates on an
OHLC(V) ``pd.DataFrame`` whose last row is the most recent CLOSED bar. These
predicates are opt-in confirmations layered on top of the S1/S2 entry logic;
they never relax a hard risk floor.

Reuse policy:
- ``adx_ok`` reuses the engine's existing ``adx`` column (no recompute).
- ``bollinger_touch`` computes its own bands so it stays self-contained.
- ``supertrend`` / ``choppiness_index`` / ``keltner_touch`` are not provided by
  ``indicators/engine.py`` and are implemented here.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def _true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder True Range = max(H-L, |H-prevC|, |L-prevC|)."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


def _wilder_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR via Wilder smoothing (equivalent to ewm with alpha = 1/period)."""
    tr = _true_range(df)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """SuperTrend direction: +1 (uptrend) / -1 (downtrend), aligned to df.index.

    Standard formulation: basic bands = HL2 ± multiplier*ATR, carried forward,
    direction flips when close crosses the opposing final band.
    """
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    hl2 = (high + low) / 2.0
    atr = _wilder_atr(df, period)

    upper = (hl2 + multiplier * atr).tolist()
    lower = (hl2 - multiplier * atr).tolist()
    closes = df["close"].astype(float).tolist()
    n = len(closes)

    final_upper: list[float] = [0.0] * n
    final_lower: list[float] = [0.0] * n
    direction: list[int] = [1] * n
    if n > 0:
        final_upper[0] = upper[0]
        final_lower[0] = lower[0]

    for i in range(1, n):
        final_upper[i] = (
            upper[i]
            if (upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            lower[i]
            if (lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )
        if closes[i] > final_upper[i - 1]:
            direction[i] = 1
        elif closes[i] < final_lower[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

    return pd.Series(direction, index=df.index, dtype="int64")


def supertrend_flip(df: pd.DataFrame, *, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """True where SuperTrend direction changed vs the prior bar (first bar False)."""
    direction = supertrend(df, period=period, multiplier=multiplier)
    flipped = direction.ne(direction.shift(1)) & direction.shift(1).notna()
    return flipped.astype(bool)


def choppiness_index(df: pd.DataFrame, *, period: int = 14) -> pd.Series:
    """Choppiness Index (0–100). Low = trending, high = choppy/range-bound."""
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    tr_sum = _true_range(df).rolling(period).sum()
    highest = high.rolling(period).max()
    lowest = low.rolling(period).min()
    span = (highest - lowest).replace(0.0, np.nan)
    return 100.0 * np.log10(tr_sum / span) / np.log10(period)


def adx_ok(df: pd.DataFrame, *, threshold: float) -> bool:
    """True iff last-bar ADX (engine column) is >= threshold. Reuses, never recomputes."""
    if df.empty or "adx" not in df.columns:
        return False
    value = df["adx"].iloc[-1]
    if value is None:
        return False
    fvalue = float(value)
    if np.isnan(fvalue):
        return False
    return fvalue >= threshold


def bollinger_touch(
    df: pd.DataFrame,
    *,
    period: int = 20,
    std: float = 2.0,
    side: Literal["upper", "lower"],
) -> bool:
    """True iff the last bar pierces the requested Bollinger band.

    Bands are self-computed from close: SMA(period) ± std * rolling-std(ddof=0).
    """
    if len(df) < period:
        return False
    close = df["close"].astype(float)
    sma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    last = df.iloc[-1]
    if side == "lower":
        lower = float(sma.iloc[-1] - std * sd.iloc[-1])
        return float(last["low"]) <= lower
    upper = float(sma.iloc[-1] + std * sd.iloc[-1])
    return float(last["high"]) >= upper


def keltner_touch(
    df: pd.DataFrame,
    *,
    period: int = 20,
    multiplier: float = 1.5,
    side: Literal["upper", "lower"],
) -> bool:
    """True iff the last bar pierces the requested Keltner channel band.

    Channel = EMA(period) ± multiplier * ATR(period), ATR via Wilder smoothing.
    """
    if len(df) < period:
        return False
    close = df["close"].astype(float)
    ema = close.ewm(span=period, adjust=False).mean()
    atr = _wilder_atr(df, period)
    last = df.iloc[-1]
    if side == "lower":
        lower = float(ema.iloc[-1] - multiplier * atr.iloc[-1])
        return float(last["low"]) <= lower
    upper = float(ema.iloc[-1] + multiplier * atr.iloc[-1])
    return float(last["high"]) >= upper


def rsi_divergence(df: pd.DataFrame, *, lookback: int) -> Literal["bullish", "bearish", "none"]:
    """Detect RSI/price divergence over the last `lookback` bars.

    Bullish: recent-half lower price-low + higher RSI-low.
    Bearish: recent-half higher price-high + lower RSI-high.
    """
    if "rsi" not in df.columns or len(df) < lookback or lookback < 2:
        return "none"
    window = df.iloc[-lookback:]
    half = lookback // 2
    older = window.iloc[:half]
    recent = window.iloc[half:]

    older_low_idx = older["low"].astype(float).idxmin()
    recent_low_idx = recent["low"].astype(float).idxmin()
    bullish = float(recent.loc[recent_low_idx, "low"]) < float(
        older.loc[older_low_idx, "low"]
    ) and float(recent.loc[recent_low_idx, "rsi"]) > float(older.loc[older_low_idx, "rsi"])

    older_high_idx = older["high"].astype(float).idxmax()
    recent_high_idx = recent["high"].astype(float).idxmax()
    bearish = float(recent.loc[recent_high_idx, "high"]) > float(
        older.loc[older_high_idx, "high"]
    ) and float(recent.loc[recent_high_idx, "rsi"]) < float(older.loc[older_high_idx, "rsi"])

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "none"
