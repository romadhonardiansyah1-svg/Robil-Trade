"""Multi-timeframe (MTF) helpers for the scan engine (SP-2).

Pure, I/O-free functions shared by the scan pipeline and the scalping
strategies (SP-4). ``h4_trend_bias`` reduces an anchor (H4) OHLC frame to a
coarse trend label and ``aligned`` answers whether a candidate action agrees
with that bias. Both are deterministic over a plain ``close`` column so they
can be unit-tested with hand-built frames and are safe to call from a worker
thread (no shared state).
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from rtrade.core.constants import Action

Bias = Literal["UP", "DOWN", "NONE"]

# Minimum closed anchor bars before a bias is meaningful (else NONE → blocks).
_MIN_BARS = 60
# EMA spans for the fast/slow trend pair and the slow-EMA slope lookback.
_EMA_FAST = 20
_EMA_SLOW = 50
_SLOPE_LOOKBACK = 10


def h4_trend_bias(df_h4: pd.DataFrame) -> Bias:
    """Coarse anchor-timeframe trend bias: UP / DOWN / NONE.

    UP   : fast EMA above slow EMA AND the slow EMA is rising over the last
           ``_SLOPE_LOOKBACK`` bars.
    DOWN : fast EMA below slow EMA AND the slow EMA is falling.
    NONE : insufficient bars, missing ``close``, or no aligned slope (flat /
           conflicting) — NONE deliberately blocks all entries downstream.
    """
    if df_h4 is None or "close" not in df_h4.columns:
        return "NONE"
    closes = df_h4["close"].astype(float).dropna()
    if len(closes) < _MIN_BARS:
        return "NONE"

    ema_fast = closes.ewm(span=_EMA_FAST, adjust=False).mean()
    ema_slow = closes.ewm(span=_EMA_SLOW, adjust=False).mean()

    fast_last = float(ema_fast.iloc[-1])
    slow_last = float(ema_slow.iloc[-1])
    slow_prev = float(ema_slow.iloc[-1 - _SLOPE_LOOKBACK])

    rising = slow_last > slow_prev
    falling = slow_last < slow_prev

    if fast_last > slow_last and rising:
        return "UP"
    if fast_last < slow_last and falling:
        return "DOWN"
    return "NONE"


def aligned(bias: Bias, action: Action) -> bool:
    """True when ``action`` agrees with ``bias``; NONE blocks everything."""
    if bias == "UP":
        return action == Action.BUY
    if bias == "DOWN":
        return action == Action.SELL
    return False
