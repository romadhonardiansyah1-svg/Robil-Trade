"""T18: Smart exit logic — partial TP, breakeven stop, trailing stop.

Used by both the backtester and paper-tracker when `smart_exits=True`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SmartExitConfig:
    """Configuration for smart exits."""

    # Partial TP: close partial_pct of position at partial_rr R.
    partial_tp_enabled: bool = True
    partial_rr: float = 1.0  # take partial at 1R
    partial_pct: float = 0.50  # close 50% of position

    # Breakeven: move SL to entry when price reaches breakeven_rr × R.
    breakeven_enabled: bool = True
    breakeven_rr: float = 1.0  # move to BE at 1R

    # Trailing: trail SL at (high - trail_atr_mult × ATR) for BUY.
    trailing_enabled: bool = True
    trail_atr_mult: float = 2.0  # 2× ATR trailing distance
    trail_activation_rr: float = 1.5  # activate after 1.5R


@dataclass
class ExitState:
    """Mutable state tracked per trade during bar-by-bar replay."""

    current_sl: float
    remaining_pct: float = 1.0  # fraction of position still open
    partial_taken: bool = False
    be_moved: bool = False
    trailing_active: bool = False
    realized_r: float = 0.0  # accumulated R from partials


def apply_smart_exit(
    state: ExitState,
    cfg: SmartExitConfig,
    *,
    direction: str,
    entry: float,
    original_sl: float,
    take_profit: float,
    bar_high: float,
    bar_low: float,
    atr: float,
) -> tuple[ExitState, str | None]:
    """Process one bar through smart exit logic.

    Returns:
        Updated state and exit_reason (None if trade continues).
    """
    sl_dist = abs(entry - original_sl) or 1.0

    if direction == "BUY":
        extreme = bar_high
        adverse = bar_low
    else:
        extreme = bar_low
        adverse = bar_high

    # Current distance from entry in R.
    if direction == "BUY":
        current_r = (extreme - entry) / sl_dist
    else:
        current_r = (entry - extreme) / sl_dist

    # --- Partial TP ---
    if cfg.partial_tp_enabled and not state.partial_taken and current_r >= cfg.partial_rr:
        partial_r = cfg.partial_rr * cfg.partial_pct
        state.realized_r += partial_r
        state.remaining_pct -= cfg.partial_pct
        state.partial_taken = True

    # --- Breakeven ---
    if cfg.breakeven_enabled and not state.be_moved and current_r >= cfg.breakeven_rr:
        state.current_sl = entry
        state.be_moved = True

    # --- Trailing ---
    if cfg.trailing_enabled and current_r >= cfg.trail_activation_rr:
        state.trailing_active = True

    if state.trailing_active and atr > 0:
        if direction == "BUY":
            trail_sl = extreme - cfg.trail_atr_mult * atr
            if trail_sl > state.current_sl:
                state.current_sl = trail_sl
        else:
            trail_sl = extreme + cfg.trail_atr_mult * atr
            if trail_sl < state.current_sl:
                state.current_sl = trail_sl

    # --- Check SL/TP hit ---
    sl_hit = False
    tp_hit = False
    if direction == "BUY":
        sl_hit = adverse <= state.current_sl
        tp_hit = extreme >= take_profit
    else:
        sl_hit = adverse >= state.current_sl
        tp_hit = extreme <= take_profit

    if sl_hit and tp_hit:
        return state, "SL"  # worst-case
    if sl_hit:
        return state, "SL"
    if tp_hit:
        return state, "TP"

    return state, None
