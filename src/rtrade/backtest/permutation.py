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
        p-value in (0, 1]. Lower is better (strategy has real edge).

    Note:
        Uses the standard small-sample correction ``(count_ge + 1) /
        (n_permutations + 1)`` (Davison & Hinkley 1997; North et al. 2002).
        Adding the observed statistic itself to the permutation null guarantees
        the p-value is strictly positive — an exact ``0`` is statistically
        invalid (it would claim zero probability of seeing the result by
        chance, which the finite Monte-Carlo sample cannot establish).
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

    # Small-sample correction: include the observed statistic in the null so
    # the p-value is strictly positive (never an invalid exact 0).
    return (count_ge + 1) / (n_permutations + 1)
