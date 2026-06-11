"""Paper-tracker — virtual fill & outcome evaluation (PLAN §8.12, ADR-12).

Runs every 15 minutes (scheduled). For each PUBLISHED signal:
1. Check if the limit order would have been filled (price touched entry_limit).
2. Once filled, track SL/TP/expiry using live candle data.
3. Update signal status: FILLED → TP_HIT / SL_HIT / EXPIRED.

This is the calibration engine — paper-trade results drive:
- Confidence calibration (§8.13)
- Kelly criterion eligibility (≥100 trades)
- Expectancy guard (GR-13)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog

from rtrade.core.constants import Action, SignalStatus
from rtrade.core.timeutil import ensure_utc

logger = structlog.get_logger(__name__)


@dataclass
class PaperTradeUpdate:
    """An update to a signal's paper-trade status."""

    signal_id: str
    new_status: SignalStatus
    resolved_at: datetime
    outcome_r: float | None = None  # R-multiple
    fill_price: float | None = None


def check_fill(
    signal_id: str,
    action: str,
    entry_limit: float,
    valid_until: datetime,
    candle_high: float,
    candle_low: float,
    candle_ts: datetime,
) -> PaperTradeUpdate | None:
    """Check if a PUBLISHED signal's limit order would have been filled.

    Returns a FILLED update if the price touched the entry limit within
    the validity period.
    """
    now = candle_ts
    valid_until = ensure_utc(valid_until)

    # Check expiry first.
    if now > valid_until:
        return PaperTradeUpdate(
            signal_id=signal_id,
            new_status=SignalStatus.EXPIRED,
            resolved_at=now,
        )

    # Check fill.
    if candle_low <= entry_limit <= candle_high:
        return PaperTradeUpdate(
            signal_id=signal_id,
            new_status=SignalStatus.FILLED,
            resolved_at=now,
            fill_price=entry_limit,
        )

    return None


def check_outcome(
    signal_id: str,
    action: str,
    entry_limit: float,
    stop_loss: float,
    take_profit: float,
    candle_high: float,
    candle_low: float,
    candle_ts: datetime,
) -> PaperTradeUpdate | None:
    """Check if a FILLED signal hit TP or SL.

    If both TP and SL are hit in the same candle → SL first (worst-case).
    """
    sl_hit = False
    tp_hit = False

    if action == Action.BUY or action == "BUY":
        sl_hit = candle_low <= stop_loss
        tp_hit = candle_high >= take_profit
    else:  # SELL
        sl_hit = candle_high >= stop_loss
        tp_hit = candle_low <= take_profit

    sl_dist = abs(entry_limit - stop_loss)
    if sl_dist == 0:
        sl_dist = 1.0  # prevent division by zero

    if sl_hit and tp_hit:
        # Worst-case: SL hit first.
        outcome_r = -1.0
        return PaperTradeUpdate(
            signal_id=signal_id,
            new_status=SignalStatus.SL_HIT,
            resolved_at=candle_ts,
            outcome_r=outcome_r,
        )
    elif sl_hit:
        outcome_r = -1.0
        return PaperTradeUpdate(
            signal_id=signal_id,
            new_status=SignalStatus.SL_HIT,
            resolved_at=candle_ts,
            outcome_r=outcome_r,
        )
    elif tp_hit:
        tp_dist = abs(take_profit - entry_limit)
        outcome_r = tp_dist / sl_dist
        return PaperTradeUpdate(
            signal_id=signal_id,
            new_status=SignalStatus.TP_HIT,
            resolved_at=candle_ts,
            outcome_r=outcome_r,
        )

    return None
