"""T20: Derivatives helper — funding rate extreme detection."""

from __future__ import annotations

from rtrade.core.constants import FUNDING_EXTREME_ABS


def is_funding_extreme(rate: float) -> bool:
    """Return True if funding rate magnitude is extreme."""
    return abs(rate) >= FUNDING_EXTREME_ABS
