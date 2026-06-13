"""T20: Derivatives helper — funding rate extreme detection."""

from __future__ import annotations

# Threshold: |funding rate| >= 0.05% per 8h is extreme
FUNDING_EXTREME_ABS: float = 0.0005


def is_funding_extreme(rate: float) -> bool:
    """Return True if funding rate magnitude is extreme."""
    return abs(rate) >= FUNDING_EXTREME_ABS
