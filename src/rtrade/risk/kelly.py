"""T26: Bayesian Kelly fraction — lower-bound posterior winrate.

Uses Beta posterior for winrate and computes Kelly from the
credible quantile (conservative lower bound) instead of point estimate.
"""

from __future__ import annotations

from scipy.stats import beta  # type: ignore[import-untyped]


def bayesian_kelly_fraction(
    wins: int,
    losses: int,
    avg_win_r: float,
    avg_loss_r: float,
    *,
    fraction: float = 0.25,
    credible_quantile: float = 0.25,
) -> float | None:
    """Kelly from lower-bound credible winrate: Beta(wins+1, losses+1).ppf(q).

    Args:
        wins: Number of winning trades.
        losses: Number of losing trades.
        avg_win_r: Average R-multiple of winners.
        avg_loss_r: Average |R-multiple| of losers (positive).
        fraction: Kelly fraction divisor (0.25 = quarter-Kelly).
        credible_quantile: Posterior quantile for conservative estimate.

    Returns:
        Fractional Kelly sizing or None if n < 30 or result <= 0.
    """
    total = wins + losses
    if total < 30:
        return None

    if avg_win_r <= 0 or avg_loss_r <= 0:
        return None

    # Beta posterior for winrate.
    p_low = float(beta.ppf(credible_quantile, wins + 1, losses + 1))

    # Kelly criterion: f* = (p * b - q) / b
    # where b = avg_win / avg_loss, p = win probability, q = 1 - p
    b = avg_win_r / avg_loss_r
    q = 1.0 - p_low
    kelly = (p_low * b - q) / b

    if kelly <= 0:
        return None

    return round(kelly * fraction, 4)
