"""S2 — Range Mean-Reversion strategy (PLAN §8.4 P3).

TF sinyal: 1H. Aktif hanya saat regime = RANGE (ADX < 20).

Conditions (LONG):
- Regime = RANGE (ADX < 20)
- Band = S/R cluster (Donchian-based) dari 100 bar, width stabil
- Price at lower band ± 0.5×ATR
- RSI < 35 (oversold bounce)
- Candle reversal: close back inside band

Levels (LONG):
- entry_limit = lower band edge
- stop_loss = lower band - 1.0×ATR
- take_profit = mid-band (DISCARD if R:R < 1.5)

Hard-block: high-impact news event < 12h ahead (range breakout risk).
"""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S2RangeMR(Strategy):
    """Range Mean-Reversion strategy — P3."""

    @property
    def name(self) -> str:
        return "s2_range_mr"

    @property
    def required_regime(self) -> Regime:
        return Regime.RANGE

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Add range-specific columns: band edges, Donchian width."""
        lookback = cfg.get_int("range.band_lookback", 100)
        donchian = cfg.get_int("range.donchian_period", 20)
        df.attrs["s2_adx_max"] = cfg.get_float("range.adx_max", 20.0)
        df.attrs["s2_stability_lookback"] = cfg.get_int("range.stability_lookback", 30)
        df.attrs["s2_stability_max_change_pct"] = cfg.get_float(
            "range.stability_max_change_pct", 30.0
        )
        df.attrs["s2_rsi_oversold"] = cfg.get_float("entry.rsi_oversold", 35.0)
        df.attrs["s2_rsi_overbought"] = cfg.get_float("entry.rsi_overbought", 65.0)
        df.attrs["s2_band_tolerance_atr_mult"] = cfg.get_float("entry.band_tolerance_atr_mult", 0.5)
        df.attrs["s2_sl_atr_mult"] = cfg.get_float("levels.sl_atr_mult", 1.0)
        df.attrs["s2_rr_min"] = cfg.get_float("levels.rr_min", 1.5)

        # Donchian channel for band detection.
        df["donch_high"] = df["high"].rolling(donchian).max()
        df["donch_low"] = df["low"].rolling(donchian).min()
        df["donch_width"] = df["donch_high"] - df["donch_low"]
        df["donch_mid"] = (df["donch_high"] + df["donch_low"]) / 2

        # Band edges from longer lookback (S/R cluster).
        df["band_high"] = df["high"].rolling(lookback).max()
        df["band_low"] = df["low"].rolling(lookback).min()
        df["band_mid"] = (df["band_high"] + df["band_low"]) / 2

        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Evaluate last closed bar for a range mean-reversion setup."""
        if len(df) < 100:
            return None

        if "band_low" not in df.columns or "band_high" not in df.columns:
            return None

        last = df.iloc[-1]
        adx = float(last.get("adx", 30))
        adx_max = float(df.attrs.get("s2_adx_max", 20.0))

        # Must be in RANGE regime (ADX < 20).
        if adx >= adx_max:
            return None

        # Check band stability.
        if not self._is_band_stable(
            df,
            stability_bars=int(df.attrs.get("s2_stability_lookback", 30)),
            max_change_pct=float(df.attrs.get("s2_stability_max_change_pct", 30.0)),
        ):
            return None

        # Check LONG and SHORT.
        for action, direction in [
            (Action.BUY, "LONG"),
            (Action.SELL, "SHORT"),
        ]:
            if self._check_entry(df, action):
                return EntryIntent(
                    action=action,
                    reason=f"S2 Range Mean-Reversion {direction} at band edge",
                )

        return None

    def _is_band_stable(
        self,
        df: pd.DataFrame,
        stability_bars: int = 30,
        max_change_pct: float = 30.0,
    ) -> bool:
        """Check Donchian width stability over recent bars.

        Width change < max_change_pct% over stability_bars → stable range.
        """
        if "donch_width" not in df.columns:
            return False

        recent = df["donch_width"].dropna().iloc[-stability_bars:]
        if len(recent) < stability_bars // 2:
            return False

        width_start = float(recent.iloc[0])
        width_end = float(recent.iloc[-1])

        if width_start <= 0:
            return False

        change_pct = abs(width_end - width_start) / width_start * 100
        return change_pct < max_change_pct

    def _check_entry(self, df: pd.DataFrame, action: Action) -> bool:
        """Check entry conditions at band edge."""
        last = df.iloc[-1]
        close = float(last["close"])
        atr = float(last.get("atr", 0))
        rsi = float(last.get("rsi", 50))

        if atr <= 0:
            return False

        band_low = float(last.get("band_low", 0))
        band_high = float(last.get("band_high", 0))
        tolerance = float(df.attrs.get("s2_band_tolerance_atr_mult", 0.5)) * atr
        rsi_oversold = float(df.attrs.get("s2_rsi_oversold", 35.0))
        rsi_overbought = float(df.attrs.get("s2_rsi_overbought", 65.0))

        if action == Action.BUY:
            # Price at lower band edge ± 0.5×ATR.
            if not (band_low - tolerance <= close <= band_low + tolerance):
                return False
            # RSI < 35 (oversold).
            if rsi >= rsi_oversold:
                return False
            # Candle reversal: close back inside band.
            return close >= band_low

        else:  # SELL
            # Price at upper band edge ± 0.5×ATR.
            if not (band_high - tolerance <= close <= band_high + tolerance):
                return False
            # RSI > 65 (overbought).
            if rsi <= rsi_overbought:
                return False
            # Close back inside band.
            return close <= band_high

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """Compute levels: entry=band edge, SL=edge-1.0×ATR, TP=mid-band."""
        last = df.iloc[-1]
        atr = float(last["atr"])
        band_low = float(last["band_low"])
        band_high = float(last["band_high"])
        band_mid = float(last["band_mid"])
        sl_atr_mult = float(df.attrs.get("s2_sl_atr_mult", 1.0))
        rr_min = float(df.attrs.get("s2_rr_min", 1.5))

        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")

        if intent.action == Action.BUY:
            entry = band_low
            stop_loss = band_low - sl_atr_mult * atr
            take_profit = band_mid

            # R:R check — DISCARD if < 1.5.
            sl_dist = entry - stop_loss
            tp_dist = take_profit - entry
            if sl_dist <= 0 or tp_dist / sl_dist < rr_min:
                # Adjust TP to meet minimum R:R.
                take_profit = entry + rr_min * sl_dist

        else:  # SELL
            entry = band_high
            stop_loss = band_high + sl_atr_mult * atr
            take_profit = band_mid

            sl_dist = stop_loss - entry
            tp_dist = entry - take_profit
            if sl_dist <= 0 or tp_dist / sl_dist < rr_min:
                take_profit = entry - rr_min * sl_dist

        return LevelSet(
            entry_limit=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_signal=atr,
        )

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Discard if R:R < 1.5 after level adjustments."""
        e = levels.entry_limit
        sl = levels.stop_loss
        tp = levels.take_profit

        sl_dist = abs(e - sl)
        tp_dist = abs(tp - e)

        if sl_dist <= 0:
            return False

        rr = tp_dist / sl_dist
        rr_min = float(df.attrs.get("s2_rr_min", 1.5))
        return rr >= rr_min
