"""T23: Virtual exit ensemble — evaluate multiple exit policies per trade.

Each filled signal is replayed with N exit policies in parallel.
Results are stored in payload["virtual_exits"] for analytics.
"""

from __future__ import annotations

from rtrade.papertrack.tracker import CandleBar


def evaluate_virtual_exits(
    action: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    atr_at_signal: float,
    candles: list[CandleBar],
) -> dict[str, dict[str, object]]:
    """Evaluate 4 exit policies on the same candle data.

    Returns per policy: {"status": str, "outcome_r": float | None}.
    """
    sl_dist = abs(entry - stop_loss) or 1.0
    results: dict[str, dict[str, object]] = {}

    # --- fixed_2r (baseline) ---
    results["fixed_2r"] = _eval_fixed(action, entry, stop_loss, take_profit, sl_dist, candles)

    # --- partial_be (50% @ +1R, SL→entry) ---
    results["partial_be"] = _eval_partial_be(
        action, entry, stop_loss, take_profit, sl_dist, candles
    )

    # --- time_stop_12 (exit at close of bar 12 if no SL/TP) ---
    results["time_stop_12"] = _eval_time_stop(
        action, entry, stop_loss, take_profit, sl_dist, candles, max_bars=12
    )

    # --- wide_tp_3r (TP at entry ± 3R) ---
    if action == "BUY":
        wide_tp = entry + 3 * sl_dist
    else:
        wide_tp = entry - 3 * sl_dist
    results["wide_tp_3r"] = _eval_fixed(action, entry, stop_loss, wide_tp, sl_dist, candles)

    return results


def _eval_fixed(
    action: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    sl_dist: float,
    candles: list[CandleBar],
) -> dict[str, object]:
    for bar in candles:
        sl_hit, tp_hit = _check_hit(action, stop_loss, take_profit, bar)
        if (sl_hit and tp_hit) or sl_hit:
            r = -(abs(entry - stop_loss) / sl_dist)
            return {"status": "SL_HIT", "outcome_r": round(r, 4)}
        if tp_hit:
            r = abs(take_profit - entry) / sl_dist
            return {"status": "TP_HIT", "outcome_r": round(r, 4)}
    return {"status": "OPEN", "outcome_r": None}


def _eval_partial_be(
    action: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    sl_dist: float,
    candles: list[CandleBar],
) -> dict[str, object]:
    partial_taken = False
    realized_r = 0.0
    current_sl = stop_loss
    remaining = 1.0

    for bar in candles:
        if action == "BUY":
            extreme = bar.high
        else:
            extreme = bar.low

        current_r = (extreme - entry) / sl_dist if action == "BUY" else (entry - extreme) / sl_dist

        if not partial_taken and current_r >= 1.0:
            realized_r += 0.5 * 1.0
            remaining = 0.5
            current_sl = entry  # BE
            partial_taken = True

        sl_hit, tp_hit = _check_hit(action, current_sl, take_profit, bar)
        if (sl_hit and tp_hit) or sl_hit:
            exit_r = (
                ((current_sl - entry) / sl_dist)
                if action == "BUY"
                else ((entry - current_sl) / sl_dist)
            )
            total_r = realized_r + remaining * exit_r
            return {"status": "SL_HIT", "outcome_r": round(total_r, 4)}
        if tp_hit:
            exit_r = abs(take_profit - entry) / sl_dist
            total_r = realized_r + remaining * exit_r
            return {"status": "TP_HIT", "outcome_r": round(total_r, 4)}

    return {"status": "OPEN", "outcome_r": None}


def _eval_time_stop(
    action: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    sl_dist: float,
    candles: list[CandleBar],
    *,
    max_bars: int,
) -> dict[str, object]:
    for i, bar in enumerate(candles):
        sl_hit, tp_hit = _check_hit(action, stop_loss, take_profit, bar)
        if (sl_hit and tp_hit) or sl_hit:
            r = -(abs(entry - stop_loss) / sl_dist)
            return {"status": "SL_HIT", "outcome_r": round(r, 4)}
        if tp_hit:
            r = abs(take_profit - entry) / sl_dist
            return {"status": "TP_HIT", "outcome_r": round(r, 4)}
        if i + 1 >= max_bars:
            close_price = bar.close if bar.close else entry
            if action == "BUY":
                r = (close_price - entry) / sl_dist
            else:
                r = (entry - close_price) / sl_dist
            return {"status": "TIME_EXIT", "outcome_r": round(r, 4)}
    return {"status": "OPEN", "outcome_r": None}


def _check_hit(
    action: str,
    stop_loss: float,
    take_profit: float,
    bar: CandleBar,
) -> tuple[bool, bool]:
    if action == "BUY":
        return bar.low <= stop_loss, bar.high >= take_profit
    return bar.high >= stop_loss, bar.low <= take_profit
