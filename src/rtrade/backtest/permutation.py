"""T25: Sign-flip permutation test for backtest validation.

Tests H0: the trading strategy has no directional edge.
Uses sign-flip bootstrap: shuffles the signs of R-multiples
and computes the proportion of permutations with mean >= actual mean.
"""

from __future__ import annotations

import numpy as np


def permutation_pvalue(
    r_multiples: list[float],
    n_permutations: int = 1000,
    seed: int = 42,
) -> float:
    """P(random expectancy >= actual expectancy) via sign-flip test.

    Args:
        r_multiples: List of R-multiples from filled trades.
        n_permutations: Number of permutations.
        seed: RNG seed for reproducibility.

    Returns:
        p-value (0..1). Lower is better (strategy has real edge).
    """
    if len(r_multiples) < 5:
        return 1.0  # not enough data

    r = np.array(r_multiples, dtype=float)
    actual_mean = float(np.mean(r))
    abs_r = np.abs(r)
    n = len(r)

    rng = np.random.default_rng(seed)
    count_ge = 0

    for _ in range(n_permutations):
        signs = rng.choice(np.array([1.0, -1.0]), size=n)
        perm_mean = float(np.mean(abs_r * signs))
        if perm_mean >= actual_mean:
            count_ge += 1

    return count_ge / n_permutations
