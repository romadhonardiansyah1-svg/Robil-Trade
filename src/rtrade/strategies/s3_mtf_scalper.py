"""S3 — MTF-Confluence Scalper (PLAN SP-4 §9.1).

Entry timeframe: M5/M15. Anchor: H4 (the scan layer enforces H4-bias alignment
via enforce_bias, so this strategy only emits entry-tf candidates and never
inspects H4 itself). Active only when regime = TREND.

Setup (LONG; mirror for SHORT):
1. Trend alignment on the entry tf: EMA20 > EMA50 and EMA20 rising.
2. Pullback to value: the trigger bar's low dips into the EMA20 value line
   (low <= EMA20) but holds structure (low > EMA50) — a deterministic
   higher-low proxy — and closes back above EMA20 (reclaim).
3. Value side of VWAP: close > VWAP (longs) / close < VWAP (shorts).
4. Momentum turn: prior-bar RSI in the dip (<= rsi_long_max) and last RSI
   turning up (rsi[-1] > rsi[-2]).
5. (Opt-in) volume filter: last volume >= volume_window mean * min_ratio.

Levels (LONG):
- entry_limit = EMA20 (limit waits for the retest into value).
- stop_loss   = entry - k*ATR, k = sl_atr_mult clamped to [0.5, 3.0] (GR-04).
- take_profit = entry + rr_target * (entry - stop_loss), rr_target >= rr_min.

The pipeline's edge_quality stage (in generate_candidate) provides the primary
adverse-selection / spread / volume rejection; S3's own volume filter is off by
default (min_ratio = 0.0).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S3MtfScalper(Strategy):
    """MTF EMA20/VWAP pullback scalper — S3."""

    @property
    def name(self) -> str:
        return "s3_mtf_scalper"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Add EMA20/EMA50/rolling-VWAP columns; stow params in df.attrs."""
        ema_fast = cfg.get_int("trend.ema_fast", 20)
        ema_mid = cfg.get_int("trend.ema_mid", 50)
        vwap_window = cfg.get_int("trend.vwap_window", 20)
        df.attrs["s3_min_bars"] = cfg.get_int("min_bars", 50)
        df.attrs["s3_ema_rising_lookback"] = cfg.get_int("trend.ema_rising_lookback", 5)
        df.attrs["s3_rsi_long_max"] = cfg.get_float("momentum.rsi_long_max", 45.0)
        df.attrs["s3_rsi_short_min"] = cfg.get_float("momentum.rsi_short_min", 55.0)
        df.attrs["s3_min_volume_ratio"] = cfg.get_float("volume.min_ratio", 0.0)
        df.attrs["s3_volume_window"] = cfg.get_int("volume.window", 20)
        df.attrs["s3_sl_atr_mult"] = cfg.get_float("levels.sl_atr_mult", 1.2)
        df.attrs["s3_rr_target"] = cfg.get_float("levels.rr_target", 1.8)

        df["s3_ema_fast"] = df["close"].astype(float).ewm(span=ema_fast, adjust=False).mean()
        df["s3_ema_mid"] = df["close"].astype(float).ewm(span=ema_mid, adjust=False).mean()

        typical = (
            df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)
        ) / 3.0
        if "volume" in df.columns:
            vol = df["volume"].astype(float).replace(0.0, np.nan)
            df["s3_vwap"] = (typical * vol).rolling(vwap_window).sum() / vol.rolling(
                vwap_window
            ).sum()
        else:
            df["s3_vwap"] = typical.rolling(vwap_window).mean()
        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Evaluate the last closed bar for a value-pullback scalp."""
        min_bars = int(df.attrs.get("s3_min_bars", 50))
        if len(df) < min_bars or "s3_ema_fast" not in df.columns:
            return None
        for action, label in [(Action.BUY, "LONG"), (Action.SELL, "SHORT")]:
            if self._check_setup(df, action):
                return EntryIntent(action=action, reason=f"S3 MTF pullback {label} at value")
        return None

    def _check_setup(self, df: pd.DataFrame, action: Action) -> bool:
        rising_lb = int(df.attrs.get("s3_ema_rising_lookback", 5))
        if len(df) <= rising_lb:
            return False

        ema_fast_series = df["s3_ema_fast"].astype(float)
        ema_mid_series = df["s3_ema_mid"].astype(float)
        ema_fast = float(ema_fast_series.iloc[-1])
        ema_mid = float(ema_mid_series.iloc[-1])
        ema_fast_prev = float(ema_fast_series.iloc[-1 - rising_lb])
        vwap = float(df["s3_vwap"].iloc[-1])
        if any(np.isnan(v) for v in (ema_fast, ema_mid, ema_fast_prev, vwap)):
            return False

        last = df.iloc[-1]
        prev = df.iloc[-2]
        low = float(last["low"])
        high = float(last["high"])
        close = float(last["close"])
        rsi_last = float(last.get("rsi", 50.0))
        rsi_prev = float(prev.get("rsi", 50.0))
        rsi_long_max = float(df.attrs.get("s3_rsi_long_max", 45.0))
        rsi_short_min = float(df.attrs.get("s3_rsi_short_min", 55.0))

        if action == Action.BUY:
            trend_ok = ema_fast > ema_mid and ema_fast > ema_fast_prev
            pullback_ok = low <= ema_fast and low > ema_mid and close > ema_fast
            value_ok = close > vwap
            momentum_ok = rsi_prev <= rsi_long_max and rsi_last > rsi_prev
        else:  # SELL
            trend_ok = ema_fast < ema_mid and ema_fast < ema_fast_prev
            pullback_ok = high >= ema_fast and high < ema_mid and close < ema_fast
            value_ok = close < vwap
            momentum_ok = rsi_prev >= rsi_short_min and rsi_last < rsi_prev

        if not (trend_ok and pullback_ok and value_ok and momentum_ok):
            return False
        return self._volume_ok(df)

    def _volume_ok(self, df: pd.DataFrame) -> bool:
        """Opt-in volume filter (default off when min_ratio == 0.0)."""
        min_ratio = float(df.attrs.get("s3_min_volume_ratio", 0.0))
        if min_ratio <= 0.0 or "volume" not in df.columns:
            return True
        window = int(df.attrs.get("s3_volume_window", 20))
        vols = df["volume"].astype(float)
        if len(vols) < window:
            return True
        mean_vol = float(vols.iloc[-window:].mean())
        return float(vols.iloc[-1]) >= mean_vol * min_ratio

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """entry = EMA20; SL = entry ∓ k*ATR (k clamped [0.5,3.0]); TP at rr_target."""
        last = df.iloc[-1]
        entry = float(last["s3_ema_fast"])
        atr = float(last["atr"])
        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")
        k = max(0.5, min(3.0, float(df.attrs.get("s3_sl_atr_mult", 1.2))))
        rr_target = float(df.attrs.get("s3_rr_target", 1.8))

        if intent.action == Action.BUY:
            stop_loss = entry - k * atr
            take_profit = entry + rr_target * (entry - stop_loss)
        else:  # SELL
            stop_loss = entry + k * atr
            take_profit = entry - rr_target * (stop_loss - entry)

        return LevelSet(
            entry_limit=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_signal=atr,
        )

    def confirm_signal(self, df: pd.DataFrame, levels: LevelSet) -> bool:
        """Discard if RR fell below rr_target after rounding."""
        sl_dist = abs(levels.entry_limit - levels.stop_loss)
        if sl_dist <= 0:
            return False
        rr = abs(levels.take_profit - levels.entry_limit) / sl_dist
        return rr >= float(df.attrs.get("s3_rr_target", 1.8))
