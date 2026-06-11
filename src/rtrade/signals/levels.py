"""Level engine -- centralised level validation and rounding (PLAN 8.5).

Ensures all price levels satisfy the invariants:
    BUY : stop_loss < entry_limit < take_profit
    SELL: take_profit < entry_limit < stop_loss
    sl_distance in [0.5*ATR, 3.0*ATR]
    rr = |tp - entry| / |entry - sl| >= rr_min (1.5)
    Prices rounded to instrument's pip_size
"""

from __future__ import annotations

import math

from rtrade.core.constants import Action
from rtrade.signals.schemas import LevelSet


def round_to_tick(price: float, pip_size: float) -> float:
    """Round a price to the nearest tick (pip_size)."""
    if pip_size <= 0:
        return price
    return round(round(price / pip_size) * pip_size, _decimals(pip_size))


def _decimals(pip_size: float) -> int:
    """Number of decimal places for a pip size."""
    if pip_size >= 1:
        return 0
    return max(0, -math.floor(math.log10(pip_size)))


def validate_and_round_levels(
    levels: LevelSet,
    action: Action,
    pip_size: float,
    *,
    rr_min: float = 1.5,
    sl_atr_min: float = 0.5,
    sl_atr_max: float = 3.0,
) -> LevelSet | None:
    """Validate level invariants and round to tick size.

    Returns a new LevelSet with rounded prices, or None if the levels
    are structurally invalid and should be discarded.
    """
    entry = round_to_tick(levels.entry_limit, pip_size)
    sl = round_to_tick(levels.stop_loss, pip_size)
    tp = round_to_tick(levels.take_profit, pip_size)
    atr = levels.atr_at_signal

    # Direction check.
    if action == Action.BUY:
        if not (sl < entry < tp):
            return None
    elif action == Action.SELL:
        if not (tp < entry < sl):
            return None
    else:
        return None

    # R:R check.
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    if sl_dist == 0:
        return None
    rr = tp_dist / sl_dist
    if rr < rr_min:
        return None

    # SL distance in ATR multiples.
    if atr <= 0:
        return None
    atr_mult = sl_dist / atr
    if not (sl_atr_min <= atr_mult <= sl_atr_max):
        return None

    # All distinct.
    if len({entry, sl, tp}) != 3:
        return None

    return LevelSet(
        entry_limit=entry,
        stop_loss=sl,
        take_profit=tp,
        atr_at_signal=atr,
    )
