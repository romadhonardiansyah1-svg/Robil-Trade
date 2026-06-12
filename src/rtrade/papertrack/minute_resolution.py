"""T22: Minute resolution for ambiguous papertrack bars.

When both SL and TP are hit in the same bar, fetch 1-minute candles
to determine which was hit first (exact resolution).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rtrade.papertrack.tracker import CandleBar


def resolve_ambiguous_bar(
    action: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    minute_candles: list[CandleBar],
) -> str:
    """Determine SL or TP hit order from minute candles.

    Returns:
        "SL" if stop loss was hit first.
        "TP" if take profit was hit first.
        "SL" if candles are empty (worst-case fallback).
    """
    if not minute_candles:
        return "SL"  # worst-case

    for bar in minute_candles:
        sl_hit = False
        tp_hit = False

        if action == "BUY":
            sl_hit = bar.low <= stop_loss
            tp_hit = bar.high >= take_profit
        else:  # SELL
            sl_hit = bar.high >= stop_loss
            tp_hit = bar.low <= take_profit

        if sl_hit and tp_hit:
            return "SL"  # worst-case on same minute bar
        if sl_hit:
            return "SL"
        if tp_hit:
            return "TP"

    return "SL"  # worst-case if neither hit in minute data
