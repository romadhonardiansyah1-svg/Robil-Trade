"""Indicator engine — pure functions, no side effects, no I/O (PLAN §8.2).

Computes all technical indicators on a DataFrame of CLOSED candles and returns
an augmented IndicatorFrame + an IndicatorSnapshot of the latest bar values.

The engine is the single source of truth for indicator values. Strategy modules
call this, not pandas_ta directly, so that all indicator logic is centralised
and covered by golden tests (§12.2).

Rules:
- The last bar in the DataFrame MUST be a closed bar (asserted).
- All functions are pure (deterministic, no I/O).
- Golden test: 300 XAUUSD 1H candles with frozen reference values.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta as ta  # type: ignore[import-untyped]


@dataclass(frozen=True, slots=True)
class IndicatorSnapshot:
    """Latest bar indicator values — used in context packs and guardrails."""

    ema21: float
    ema50: float
    ema200: float
    rsi: float
    atr: float
    adx: float
    plus_di: float
    minus_di: float
    macd: float
    macd_signal: float
    macd_hist: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    vwap: float | None  # None if volume unavailable
    atr_percentile: float
    bar_ts: pd.Timestamp


def compute(
    df: pd.DataFrame,
    *,
    ema_periods: tuple[int, int, int] = (21, 50, 200),
    rsi_period: int = 14,
    atr_period: int = 14,
    adx_period: int = 14,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    bb_length: int = 20,
    bb_std: float = 2.0,
    atr_percentile_window: int = 252,
) -> pd.DataFrame:
    """Compute all indicators on OHLCV DataFrame.

    Expects columns: open, high, low, close, volume (optional).
    Index should be datetime (bar open time).
    Returns the same DataFrame augmented with indicator columns.
    """
    assert len(df) > 0, "empty DataFrame"
    assert all(col in df.columns for col in ("open", "high", "low", "close")), (
        "missing OHLC columns"
    )

    # Ensure float dtype for calculations.
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    if "volume" in df.columns:
        df["volume"] = df["volume"].astype(float)

    # --- EMAs ---
    for period in ema_periods:
        df[f"ema{period}"] = ta.ema(df["close"], length=period)

    # --- RSI ---
    df["rsi"] = ta.rsi(df["close"], length=rsi_period)

    # --- ATR ---
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=atr_period)

    # --- ADX + DI ---
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=adx_period)
    if adx_df is not None:
        df["adx"] = adx_df[f"ADX_{adx_period}"]
        df["plus_di"] = adx_df[f"DMP_{adx_period}"]
        df["minus_di"] = adx_df[f"DMN_{adx_period}"]

    # --- MACD ---
    macd_df = ta.macd(df["close"], fast=macd_fast, slow=macd_slow, signal=macd_signal)
    if macd_df is not None:
        df["macd"] = macd_df[f"MACD_{macd_fast}_{macd_slow}_{macd_signal}"]
        df["macd_signal"] = macd_df[f"MACDs_{macd_fast}_{macd_slow}_{macd_signal}"]
        df["macd_hist"] = macd_df[f"MACDh_{macd_fast}_{macd_slow}_{macd_signal}"]

    # --- Bollinger Bands ---
    bb_df = ta.bbands(df["close"], length=bb_length, std=bb_std)
    if bb_df is not None:
        # pandas-ta column names vary by version (e.g. BBU_20_2.0 vs BBU_20_2).
        # Find columns dynamically by prefix.
        bb_cols = bb_df.columns.tolist()
        bbu = [c for c in bb_cols if c.startswith("BBU_")]
        bbm = [c for c in bb_cols if c.startswith("BBM_")]
        bbl = [c for c in bb_cols if c.startswith("BBL_")]
        if bbu and bbm and bbl:
            df["bb_upper"] = bb_df[bbu[0]]
            df["bb_mid"] = bb_df[bbm[0]]
            df["bb_lower"] = bb_df[bbl[0]]

    # --- VWAP (rolling daily — only if volume available) ---
    if "volume" in df.columns and df["volume"].sum() > 0:
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        cumvol = df["volume"].cumsum()
        cumtp = (typical_price * df["volume"]).cumsum()
        df["vwap"] = cumtp / cumvol.replace(0, np.nan)
    else:
        df["vwap"] = np.nan

    # --- ATR percentile (rolling window) ---
    if "atr" in df.columns:
        df["atr_percentile"] = (
            df["atr"]
            .rolling(window=min(atr_percentile_window, len(df)), min_periods=1)
            .apply(lambda x: _percentile_rank(x), raw=True)
        )
    else:
        df["atr_percentile"] = np.nan

    return df


def _percentile_rank(series: np.ndarray) -> float:  # type: ignore[type-arg]
    """Percentile rank of the last value within the window (0–100)."""
    if len(series) < 2:
        return 50.0
    last = series[-1]
    count_below = np.sum(series[:-1] < last)
    return float(count_below / (len(series) - 1) * 100)


def snapshot(df: pd.DataFrame) -> IndicatorSnapshot:
    """Extract IndicatorSnapshot from the last row of an indicator DataFrame."""
    if df.empty:
        raise ValueError("cannot snapshot an empty DataFrame")

    last = df.iloc[-1]

    def _get(col: str, default: float = 0.0) -> float:
        val = last.get(col)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return default
        return float(val)

    return IndicatorSnapshot(
        ema21=_get("ema21"),
        ema50=_get("ema50"),
        ema200=_get("ema200"),
        rsi=_get("rsi", 50.0),
        atr=_get("atr"),
        adx=_get("adx"),
        plus_di=_get("plus_di"),
        minus_di=_get("minus_di"),
        macd=_get("macd"),
        macd_signal=_get("macd_signal"),
        macd_hist=_get("macd_hist"),
        bb_upper=_get("bb_upper"),
        bb_mid=_get("bb_mid"),
        bb_lower=_get("bb_lower"),
        vwap=_get("vwap") if not np.isnan(last.get("vwap", np.nan)) else None,
        atr_percentile=_get("atr_percentile", 50.0),
        bar_ts=pd.Timestamp(df.index[-1]),
    )
