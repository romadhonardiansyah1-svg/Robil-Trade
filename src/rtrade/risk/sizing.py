"""Position sizing module (PLAN §8.7).

1. Base sizing: (equity × risk_pct) / sl_distance_in_quote
2. Fractional Kelly (¼ Kelly): ONLY if ≥100 paper-trades exist for strategy
3. Output includes "risiko dalam USD" for user transparency
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Position sizing calculation result."""

    position_size: float  # lots/contracts/qty
    risk_amount_usd: float  # risiko dalam USD (TRUE risk of position_size)
    risk_pct: float
    kelly_fraction: float | None  # None if not enough data
    kelly_size: float | None  # None if not enough data
    method: str  # "fixed_pct", "fixed_pct_with_kelly", or "abstain_min_lot"
    kelly_risk_usd: float | None = None  # TRUE USD risk of kelly_size (None if no Kelly)


def compute_position_size(
    equity: float,
    risk_pct: float,
    sl_distance: float,
    *,
    pip_size: float = 0.01,
    lot_step: float | None = None,
) -> SizingResult:
    """Compute position size based on fixed percentage risk (PLAN §8.7).

    Args:
        equity: Account equity in quote currency.
        risk_pct: Risk percentage (e.g. 1.0 for 1%). Max 2.0 (GR-05).
        sl_distance: Distance from entry to SL in quote currency.
        pip_size: Instrument's pip size for rounding.
        lot_step: Minimum lot increment (e.g. 0.01 for forex).
    """
    if equity <= 0 or risk_pct <= 0 or sl_distance <= 0:
        raise ValueError("equity, risk_pct, and sl_distance must be positive")
    if risk_pct > 2.0:
        raise ValueError(f"GR-05: risk_pct {risk_pct}% exceeds 2.0% cap")

    risk_amount = equity * (risk_pct / 100)
    position_size = risk_amount / sl_distance

    # Round DOWN to lot step if provided. Rounding down guarantees the resulting
    # size never risks more than the budget.
    if lot_step and lot_step > 0:
        position_size = math.floor(position_size / lot_step) * lot_step

        # B3: if the floored size is <= 0, even ONE lot_step would risk MORE than
        # the budget (risk_amount). Bumping up to one lot_step would silently
        # breach GR-05, so ABSTAIN instead — never over-risk.
        if position_size <= 0:
            return SizingResult(
                position_size=0.0,
                risk_amount_usd=0.0,
                risk_pct=risk_pct,
                kelly_fraction=None,
                kelly_size=None,
                method="abstain_min_lot",
                kelly_risk_usd=None,
            )

    # Without a lot_step the size is the exact (unrounded) budget-derived size.
    if position_size <= 0:
        position_size = pip_size

    # B3: report the TRUE USD risk of the (rounded-down) size. Because rounding is
    # always downward, this is <= the budget and accurately reflects what is at risk.
    true_risk_usd = round(position_size * sl_distance, 2)

    return SizingResult(
        position_size=round(position_size, 8),
        risk_amount_usd=true_risk_usd,
        risk_pct=risk_pct,
        kelly_fraction=None,
        kelly_size=None,
        method="fixed_pct",
        kelly_risk_usd=None,
    )


def compute_kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
    *,
    fraction: float = 0.25,  # ¼ Kelly
) -> float | None:
    """Compute fractional Kelly criterion (PLAN §8.7).

    Kelly % = (win_rate × avg_win - (1-win_rate) × avg_loss) / avg_win
    Then multiply by `fraction` (default ¼ for safety).

    Returns None if result is non-positive (don't bet).
    """
    if avg_win <= 0 or avg_loss <= 0 or not (0 < win_rate < 1):
        return None

    kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
    if kelly <= 0:
        return None

    return round(kelly * fraction, 4)


def compute_with_kelly(
    equity: float,
    risk_pct: float,
    sl_distance: float,
    win_rate: float,
    avg_win_r: float,
    avg_loss_r: float,
    *,
    pip_size: float = 0.01,
    lot_step: float | None = None,
    kelly_fraction: float = 0.25,
) -> SizingResult:
    """Compute sizing with both fixed-pct and Kelly (PLAN §8.7).

    Kelly is shown as a secondary suggestion, not the primary sizing.
    Requires ≥100 paper-trades (checked by caller).
    """
    base = compute_position_size(
        equity, risk_pct, sl_distance, pip_size=pip_size, lot_step=lot_step
    )

    kelly_f = compute_kelly_fraction(win_rate, avg_win_r, avg_loss_r, fraction=kelly_fraction)

    if kelly_f is not None:
        # B4: clamp the Kelly-implied risk to the GR-05 2% cap. Quarter-Kelly can
        # still imply 5–15% risk, so without this clamp the advisory would exceed
        # the per-trade risk cap. min() keeps it at or below 2% of equity.
        kelly_risk = min(equity * kelly_f, equity * 0.02)
        kelly_size = kelly_risk / sl_distance
        if lot_step and lot_step > 0:
            kelly_size = math.floor(kelly_size / lot_step) * lot_step

        # If the lot-rounded Kelly size floors to 0, there is no advisable size.
        if kelly_size <= 0:
            return base

        # B4: report the Kelly suggestion's TRUE USD risk (rounded-down size, so
        # this is accurate and <= the clamped budget). Primary sizing stays the
        # base fixed-pct size; Kelly remains a secondary/advisory suggestion.
        return SizingResult(
            position_size=base.position_size,
            risk_amount_usd=base.risk_amount_usd,
            risk_pct=base.risk_pct,
            kelly_fraction=kelly_f,
            kelly_size=round(kelly_size, 8),
            method="fixed_pct_with_kelly",
            kelly_risk_usd=round(kelly_size * sl_distance, 2),
        )

    return base
