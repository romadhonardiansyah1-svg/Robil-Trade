"""S1 — Trend-Pullback strategy (PLAN §8.4).

TF sinyal: 1H, konfirmasi: 4H.
Aktif hanya saat regime = TREND.

Filter trend (semua wajib true untuk LONG; mirror untuk SHORT):
1. close > EMA200 pada 1H DAN 4H (multi-TF agreement)
2. EMA50 > EMA200 pada 1H (golden alignment)
3. ADX(14) ≥ 20 pada 1H
4. Regime = TREND

Setup pullback (LONG):
5. Low candle menyentuh/menembus zona pullback = [EMA21 .. EMA50] dalam ≤ 3 bar terakhir
6. 35 ≤ RSI(14) ≤ 55 saat sentuh zona (dip sehat, bukan reversal)
7. Trigger: candle close kembali DI ATAS EMA21, ATAU bullish engulfing di dalam zona

Level (LONG):
- entry_limit = max(EMA21, mid-zona pullback) - limit order menunggu retest
- stop_loss = min(swing_low_terdekat - 0.25xATR, entry - 1.0xATR);
  clamp [0.5xATR, 3.0xATR]
- take_profit = entry + rr_target x (entry - stop_loss)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.indicators.structure import detect_swing_points
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S1TrendPullback(Strategy):
    """Trend-Pullback strategy — P1 primary strategy."""

    @property
    def name(self) -> str:
        return "s1_trend_pullback"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Add S1-specific columns (zone flags, pullback detection)."""
        ema_fast = cfg.get_int("trend.ema_fast", 21)
        ema_mid = cfg.get_int("trend.ema_mid", 50)

        # Pullback zone boundaries.
        df["zone_low"] = df[f"ema{ema_fast}"]
        df["zone_high"] = df[f"ema{ema_mid}"]

        # Mid-zone for entry price calculation.
        df["zone_mid"] = (df["zone_low"] + df["zone_high"]) / 2

        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Evaluate the last closed bar for a trend-pullback setup."""
        if len(df) < 200:
            return None

        # We check LONG and SHORT separately.
        for action, direction in [(Action.BUY, "LONG"), (Action.SELL, "SHORT")]:
            if self._check_trend_filter(df, action) and self._check_pullback_setup(df, action):
                return EntryIntent(
                    action=action,
                    reason=f"S1 Trend-Pullback {direction} setup triggered",
                )

        return None

    def _check_trend_filter(self, df: pd.DataFrame, action: Action) -> bool:
        """Check the 4 trend filter conditions (PLAN §8.4)."""
        last = df.iloc[-1]

        ema200 = last.get("ema200")
        ema50 = last.get("ema50")
        ema21 = last.get("ema21")
        close = float(last["close"])
        adx = last.get("adx", 0)

        if any(
            v is None or (isinstance(v, float) and np.isnan(v)) for v in [ema200, ema50, ema21, adx]
        ):
            return False

        ema200 = float(ema200)
        ema50 = float(ema50)
        adx = float(adx)

        if action == Action.BUY:
            # Filter 1: close > EMA200 (1H — we check this TF only, 4H checked externally)
            if close <= ema200:
                return False
            # Filter 2: EMA50 > EMA200 (golden alignment)
            if ema50 <= ema200:
                return False
        else:  # SELL
            if close >= ema200:
                return False
            if ema50 >= ema200:
                return False

        # Filter 3: ADX ≥ 20
        return adx >= 20

    def _check_pullback_setup(self, df: pd.DataFrame, action: Action, lookback: int = 3) -> bool:
        """Check pullback setup conditions (PLAN 8.4).

        5. Low candle touches/breaks pullback zone [EMA21..EMA50] within last 3 bars.
        6. RSI between 35–55 at touch.
        7. Trigger: close back above EMA21 (LONG) or below EMA21 (SHORT),
           OR engulfing pattern in zone.
        """
        if "zone_low" not in df.columns:
            return False

        last_bars = df.iloc[-lookback:]
        ema21_last = float(df.iloc[-1].get("ema21", 0))
        close_last = float(df.iloc[-1]["close"])

        zone_touched = False
        rsi_in_range = False

        for _, bar in last_bars.iterrows():
            zone_lo = float(bar.get("zone_low", 0))
            zone_hi = float(bar.get("zone_high", 0))

            if zone_lo == 0 or zone_hi == 0:
                continue

            # Normalise zone bounds (EMA21 might be above or below EMA50).
            z_min = min(zone_lo, zone_hi)
            z_max = max(zone_lo, zone_hi)

            if action == Action.BUY:
                # 5. Low touches or enters pullback zone.
                if float(bar["low"]) <= z_max:
                    zone_touched = True
                    # 6. RSI check at touch.
                    rsi = float(bar.get("rsi", 50))
                    if 35 <= rsi <= 55:
                        rsi_in_range = True
            else:  # SELL
                # Mirror: high touches or enters zone.
                if float(bar["high"]) >= z_min:
                    zone_touched = True
                    rsi = float(bar.get("rsi", 50))
                    if 45 <= rsi <= 65:  # mirror RSI range
                        rsi_in_range = True

        if not (zone_touched and rsi_in_range):
            return False

        # 7. Trigger: close back outside zone.
        if action == Action.BUY:
            if close_last > ema21_last:
                return True
            # Check for bullish engulfing in zone.
            return self._is_engulfing(df, action)
        else:
            if close_last < ema21_last:
                return True
            return self._is_engulfing(df, action)

    @staticmethod
    def _is_engulfing(df: pd.DataFrame, action: Action) -> bool:
        """Simple engulfing detection on the last 2 bars."""
        if len(df) < 2:
            return False
        prev = df.iloc[-2]
        curr = df.iloc[-1]

        if action == Action.BUY:
            # Bullish engulfing: current close > prev open, current open < prev close.
            return (
                float(curr["close"]) > float(prev["open"])
                and float(curr["open"]) < float(prev["close"])
                and float(curr["close"]) > float(curr["open"])
                and float(prev["close"]) < float(prev["open"])
            )
        else:
            # Bearish engulfing.
            return (
                float(curr["close"]) < float(prev["open"])
                and float(curr["open"]) > float(prev["close"])
                and float(curr["close"]) < float(curr["open"])
                and float(prev["close"]) > float(prev["open"])
            )

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """Compute deterministic entry, SL, TP levels (PLAN §8.4)."""
        last = df.iloc[-1]
        ema21 = float(last["ema21"])
        zone_mid = float(last.get("zone_mid", ema21))
        atr = float(last["atr"])

        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")

        # Find nearest swing point for SL placement.
        swing_points = detect_swing_points(df.tail(50))

        if intent.action == Action.BUY:
            entry = max(ema21, zone_mid)

            # SL: nearest swing low - 0.25*ATR, or entry - 1.0*ATR fallback.
            swing_lows = [p for p in swing_points if not p.is_high and p.price < entry]
            if swing_lows:
                nearest_swing = max(swing_lows, key=lambda p: p.price)
                sl_candidate = nearest_swing.price - 0.25 * atr
            else:
                sl_candidate = entry - 1.0 * atr

            # Clamp SL distance to [0.5*ATR, 3.0*ATR].
            sl_dist = entry - sl_candidate
            sl_dist_clamped = max(0.5 * atr, min(3.0 * atr, sl_dist))
            stop_loss = entry - sl_dist_clamped

            # TP: entry + rr_target * (entry - SL).
            rr_target = 2.0  # default, overridden by config in engine
            take_profit = entry + rr_target * (entry - stop_loss)

        else:  # SELL
            entry = min(ema21, zone_mid)

            # SL: nearest swing high + 0.25*ATR.
            swing_highs = [p for p in swing_points if p.is_high and p.price > entry]
            if swing_highs:
                nearest_swing = min(swing_highs, key=lambda p: p.price)
                sl_candidate = nearest_swing.price + 0.25 * atr
            else:
                sl_candidate = entry + 1.0 * atr

            sl_dist = sl_candidate - entry
            sl_dist_clamped = max(0.5 * atr, min(3.0 * atr, sl_dist))
            stop_loss = entry + sl_dist_clamped

            rr_target = 2.0
            take_profit = entry - rr_target * (stop_loss - entry)

        return LevelSet(
            entry_limit=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_signal=atr,
        )

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Discard if ATR clamping made SL structurally unsound."""
        # If we're buying and SL is above the nearest swing low, that's bad.
        swing_points = detect_swing_points(df.tail(50))

        if levels.entry_limit > levels.stop_loss:
            # BUY: check SL isn't above structure.
            swing_lows = [p for p in swing_points if not p.is_high and p.price < levels.entry_limit]
            if swing_lows:
                nearest = max(swing_lows, key=lambda p: p.price)
                if levels.stop_loss > nearest.price:
                    return False  # SL above swing low = structurally unsound
        else:
            # SELL: check SL isn't below structure.
            swing_highs = [p for p in swing_points if p.is_high and p.price > levels.entry_limit]
            if swing_highs:
                nearest = min(swing_highs, key=lambda p: p.price)
                if levels.stop_loss < nearest.price:
                    return False

        return True
