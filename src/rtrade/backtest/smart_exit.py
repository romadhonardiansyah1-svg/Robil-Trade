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

    Uses a pessimistic intrabar model (A2): the adverse extreme is assumed to
    occur before the favorable extreme. The stop as it stood at the START of the
    bar is tested first; if hit, the trade exits at that pre-update stop and no
    partial / breakeven / trailing update is applied on this bar. Only if the
    stop survives are the favorable-extreme updates applied (affecting subsequent
    bars) and TP checked.

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

    # --- Pessimistic intrabar ordering (A2) ---
    # Assume the ADVERSE extreme occurs BEFORE the favorable extreme. First test
    # the stop AS IT WAS AT BAR START (before any update this bar). If it is hit,
    # exit at that pre-update stop and do NOT take a partial / move BE / trail on
    # this bar. This also preserves the worst-case rule: if both the stop and TP
    # would be touched this bar, the stop wins.
    stop_at_bar_start = state.current_sl
    if direction == "BUY":
        sl_hit = adverse <= stop_at_bar_start
        tp_hit = extreme >= take_profit
    else:
        sl_hit = adverse >= stop_at_bar_start
        tp_hit = extreme <= take_profit

    if sl_hit:
        return state, "SL"

    # Stop survived the adverse extreme. Now apply favorable-extreme updates
    # (partial TP / breakeven / trailing) which take effect on SUBSEQUENT bars,
    # then check TP against the favorable extreme.

    # Current distance from entry in R (favorable extreme).
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

    if tp_hit:
        return state, "TP"

    return state, None
