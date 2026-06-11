"""Rule-based regime classification (PLAN §8.3 — P1).

Classifies market state as TREND / RANGE / CRISIS based on ADX and ATR
percentile. Includes hysteresis to prevent flip-flopping in the transition
zone (20 ≤ ADX < 25).

Rules:
    CRISIS : ATR_percentile ≥ 95  OR  |return_24h| ≥ 3 × stdev(return_24h, 90d)
    TREND  : ADX ≥ 25
    RANGE  : ADX < 20
    TRANSITION (20 ≤ ADX < 25) : use previous regime (hysteresis)

CRISIS → blocks ALL new signals for the instrument (GR-08).
S1 only active in TREND; S2 only in RANGE.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from rtrade.core.constants import Regime


@dataclass(frozen=True, slots=True)
class RegimeState:
    """Current regime classification for an instrument."""

    regime: Regime
    since: datetime  # when the regime started
    adx: float
    atr_percentile: float
    return_24h: float | None = None
    return_24h_stdev: float | None = None


class RegimeClassifier:
    """Stateful regime classifier with hysteresis (PLAN §8.3).

    Holds the previous regime per instrument to implement hysteresis
    in the ADX transition zone (20–25).
    """

    def __init__(
        self,
        *,
        crisis_atr_pct: float = 95.0,
        crisis_return_sigma: float = 3.0,
        trend_adx_min: float = 25.0,
        range_adx_max: float = 20.0,
    ) -> None:
        self._crisis_atr_pct = crisis_atr_pct
        self._crisis_return_sigma = crisis_return_sigma
        self._trend_adx_min = trend_adx_min
        self._range_adx_max = range_adx_max
        # Previous regime per instrument (for hysteresis).
        self._prev: dict[str, RegimeState] = {}

    def classify(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        now: datetime | None = None,
    ) -> RegimeState:
        """Classify the current regime from an indicator DataFrame.

        Expects columns: adx, atr_percentile, close.
        Uses the last row for current values and rolling statistics for CRISIS.
        """
        if df.empty:
            raise ValueError("cannot classify regime from empty DataFrame")

        last = df.iloc[-1]
        adx = float(last.get("adx", 0))
        atr_pct = float(last.get("atr_percentile", 50))

        # Compute 24h return and its 90-day rolling stdev.
        close = df["close"].astype(float)
        return_24h: float | None = None
        return_stdev: float | None = None
        if len(close) >= 2:
            return_24h = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)
            if len(close) >= 90:
                returns = close.pct_change().dropna().iloc[-90:]
                return_stdev = float(returns.std() * 100)

        ts = now or pd.Timestamp(df.index[-1]).to_pydatetime()

        # --- CRISIS check ---
        is_crisis = atr_pct >= self._crisis_atr_pct
        if (
            not is_crisis
            and return_24h is not None
            and return_stdev is not None
            and return_stdev > 0
        ) and abs(return_24h) >= self._crisis_return_sigma * return_stdev:
            is_crisis = True

        if is_crisis:
            regime = Regime.CRISIS
        elif adx >= self._trend_adx_min:
            regime = Regime.TREND
        elif adx < self._range_adx_max:
            regime = Regime.RANGE
        else:
            # Transition zone: hysteresis — use previous regime.
            prev = self._prev.get(symbol)
            if prev is not None and prev.regime != Regime.CRISIS:
                regime = prev.regime
            else:
                # No previous or previous was CRISIS → default to RANGE.
                regime = Regime.RANGE

        # Determine 'since' timestamp.
        prev = self._prev.get(symbol)
        since = prev.since if prev is not None and prev.regime == regime else ts

        state = RegimeState(
            regime=regime,
            since=since,
            adx=adx,
            atr_percentile=atr_pct,
            return_24h=return_24h,
            return_24h_stdev=return_stdev,
        )
        self._prev[symbol] = state
        return state

    def get_previous(self, symbol: str) -> RegimeState | None:
        """Get the last classified regime for an instrument."""
        return self._prev.get(symbol)

    def reset(self, symbol: str | None = None) -> None:
        """Reset state (for testing or instrument removal)."""
        if symbol is not None:
            self._prev.pop(symbol, None)
        else:
            self._prev.clear()
