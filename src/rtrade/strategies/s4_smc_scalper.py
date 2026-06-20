"""S4 — SMC/ICT Scalper (PLAN SP-4 §9.2).

Entry timeframe: M5/M15. Anchor: H4 (the scan layer enforces H4-bias alignment
via enforce_bias). Active only when regime = TREND.

Setup (LONG; mirror for SHORT):
1. A low-side liquidity sweep (stop-run below a prior swing low, close back
   inside) occurs within `sweep_window` bars before...
2. ...a bullish structure break (BOS or CHoCH) in the bias direction.
3. A bullish order block (last opposing candle before the break) or, as a
   fallback, a bullish fair value gap, exists at/before the break — that zone
   is where the limit waits for the retest.

Levels (LONG):
- entry_limit = order-block / FVG top (retest into the institutional zone).
- stop_loss   = swept level - sl_buffer_atr*ATR (beyond the liquidity grab);
                distance clamped to [0.5, 3.0]*ATR (GR-04).
- take_profit = entry + rr_target * risk (toward the next liquidity pool),
                rr_target >= rr_min.

Uses only the SP-3 pinned detectors in rtrade.indicators.smc.
"""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action, Regime
from rtrade.indicators.smc import (
    FairValueGap,
    OrderBlock,
    fair_value_gaps,
    liquidity_sweeps,
    market_structure,
    order_blocks,
)
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class S4SmcScalper(Strategy):
    """Liquidity-sweep + BOS/CHoCH into order-block/FVG scalper — S4."""

    @property
    def name(self) -> str:
        return "s4_smc_scalper"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        """Stow SMC params in df.attrs (detectors run on raw OHLC at entry time)."""
        df.attrs["s4_swing_lookback"] = cfg.get_int("smc.swing_lookback", 3)
        df.attrs["s4_sweep_window"] = cfg.get_int("smc.sweep_window", 8)
        df.attrs["s4_min_bars"] = cfg.get_int("min_bars", 30)
        df.attrs["s4_sl_buffer_atr"] = cfg.get_float("levels.sl_buffer_atr", 0.25)
        df.attrs["s4_rr_target"] = cfg.get_float("levels.rr_target", 1.8)
        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        """Detect a sweep -> BOS/CHoCH -> order-block/FVG setup on the last bar."""
        if len(df) < int(df.attrs.get("s4_min_bars", 30)):
            return None
        sl_lb = int(df.attrs.get("s4_swing_lookback", 3))
        sweep_window = int(df.attrs.get("s4_sweep_window", 8))

        events = market_structure(df, swing_lookback=sl_lb)
        if not events:
            return None
        sweeps = liquidity_sweeps(df, swing_lookback=sl_lb)
        obs = order_blocks(df, swing_lookback=sl_lb)
        fvgs = fair_value_gaps(df)

        for action, direction, sweep_side, label in [
            (Action.BUY, "bullish", "low", "LONG"),
            (Action.SELL, "bearish", "high", "SHORT"),
        ]:
            dir_events = [e for e in events if e.direction == direction]
            if not dir_events:
                continue
            event = max(dir_events, key=lambda e: e.idx)

            qual_sweeps = [
                s
                for s in sweeps
                if s.side == sweep_side and event.idx - sweep_window <= s.idx <= event.idx
            ]
            if not qual_sweeps:
                continue
            sweep = max(qual_sweeps, key=lambda s: s.idx)

            zone = self._entry_zone(obs, fvgs, direction, event.idx)
            if zone is None:
                continue
            top, bottom = zone

            df.attrs["s4_entry_top"] = top
            df.attrs["s4_entry_bottom"] = bottom
            df.attrs["s4_sweep_level"] = sweep.level
            return EntryIntent(
                action=action,
                reason=f"S4 SMC {label}: sweep@{sweep.idx} -> {event.kind}@{event.idx}",
            )
        return None

    @staticmethod
    def _entry_zone(
        obs: list[OrderBlock],
        fvgs: list[FairValueGap],
        direction: str,
        event_idx: int,
    ) -> tuple[float, float] | None:
        """Most-recent directional order block at/before the break, else a FVG."""
        dir_obs = [o for o in obs if o.direction == direction and o.idx <= event_idx]
        if dir_obs:
            ob = max(dir_obs, key=lambda o: o.idx)
            return (float(ob.top), float(ob.bottom))
        dir_fvgs = [f for f in fvgs if f.direction == direction and f.end_idx <= event_idx]
        if dir_fvgs:
            fvg = max(dir_fvgs, key=lambda f: f.end_idx)
            return (float(fvg.top), float(fvg.bottom))
        return None

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        """entry at the OB/FVG edge; SL beyond the sweep; TP at rr_target."""
        last = df.iloc[-1]
        atr = float(last["atr"])
        if atr <= 0:
            raise ValueError("ATR must be positive for level computation")
        entry_top = float(df.attrs["s4_entry_top"])
        entry_bottom = float(df.attrs["s4_entry_bottom"])
        sweep_level = float(df.attrs["s4_sweep_level"])
        buffer_atr = float(df.attrs.get("s4_sl_buffer_atr", 0.25))
        rr_target = float(df.attrs.get("s4_rr_target", 1.8))

        if intent.action == Action.BUY:
            entry = entry_top
            raw_sl = sweep_level - buffer_atr * atr
            sl_dist = max(0.5 * atr, min(3.0 * atr, entry - raw_sl))
            stop_loss = entry - sl_dist
            take_profit = entry + rr_target * sl_dist
        else:  # SELL
            entry = entry_bottom
            raw_sl = sweep_level + buffer_atr * atr
            sl_dist = max(0.5 * atr, min(3.0 * atr, raw_sl - entry))
            stop_loss = entry + sl_dist
            take_profit = entry - rr_target * sl_dist

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
        return rr >= float(df.attrs.get("s4_rr_target", 1.8))
