"""Signal rate limits and expectancy guard (PLAN §8.7, GR-12/GR-13).

GR-12: Max signals per day per instrument (default 3).
GR-13: If rolling N paper-trades have expectancy < 0, auto-disable strategy.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def check_daily_limit(
    signals_today: int,
    max_per_day: int = 3,
) -> tuple[bool, str | None]:
    """GR-12: Check daily signal rate limit.

    Returns (allowed, reason). True if under limit.
    """
    if signals_today >= max_per_day:
        return False, (
            f"GR-12: daily limit reached ({signals_today}/{max_per_day} "
            f"signals today for this instrument)"
        )
    return True, None


def check_expectancy_guard(
    paper_outcomes: list[float],
    window: int = 30,
) -> tuple[bool, str | None]:
    """GR-13: Check rolling expectancy of paper trades.

    If the last `window` paper trades have negative expectancy,
    the strategy should be disabled.

    Args:
        paper_outcomes: List of R-multiples from resolved paper trades.
        window: Rolling window size.

    Returns:
        (ok, reason): True if expectancy is acceptable.
    """
    if len(paper_outcomes) < window:
        # Not enough data yet — allow (can't judge).
        return True, None

    recent = paper_outcomes[-window:]
    expectancy = sum(recent) / len(recent)

    if expectancy < 0:
        return False, (
            f"GR-13: rolling {window}-trade expectancy is {expectancy:.3f} "
            f"(negative) — strategy should be disabled until review"
        )

    return True, None


def compute_expectancy(outcomes: list[float]) -> float | None:
    """Compute expectancy (average R-multiple) from trade outcomes.

    Returns None if no trades.
    """
    if not outcomes:
        return None
    return sum(outcomes) / len(outcomes)


def compute_win_rate(outcomes: list[float]) -> float | None:
    """Compute win rate from R-multiple outcomes.

    A trade is a "win" if outcome > 0.
    Returns None if no trades.
    """
    if not outcomes:
        return None
    wins = sum(1 for r in outcomes if r > 0)
    return wins / len(outcomes)


def throttled_risk_pct(
    base_risk_pct: float,
    recent_outcomes: list[float],
    *,
    window: int = 10,
    mult: float = 0.5,
) -> float:
    """T27: Return risk_pct, throttled down if recent expectancy is negative.

    Args:
        base_risk_pct: Normal risk percentage.
        recent_outcomes: List of recent R-multiples (newest last).
        window: Rolling window size.
        mult: Multiplier when throttled (0 < mult < 1).

    Returns:
        base_risk_pct or base_risk_pct * mult (never > base).
    """
    if len(recent_outcomes) < window:
        return base_risk_pct

    window_outcomes = recent_outcomes[-window:]
    expectancy = sum(window_outcomes) / len(window_outcomes)

    if expectancy < 0:
        return base_risk_pct * mult

    return base_risk_pct
