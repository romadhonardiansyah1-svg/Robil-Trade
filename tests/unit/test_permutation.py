"""T25: Tests for permutation pvalue."""

from rtrade.backtest.permutation import permutation_pvalue


def test_strong_edge_low_pvalue() -> None:
    """30 trades all +2R should have very low p-value."""
    r = [2.0] * 30
    p = permutation_pvalue(r, n_permutations=1000, seed=42)
    assert p < 0.01


def test_random_symmetric_high_pvalue() -> None:
    """Equal +1/-1 trades should have high p-value."""
    r = [1.0, -1.0] * 15
    p = permutation_pvalue(r, n_permutations=1000, seed=42)
    assert p > 0.2


def test_deterministic_seed() -> None:
    """Same seed produces same result."""
    r = [2.0, -1.0, 1.5, -0.8, 0.3] * 6
    p1 = permutation_pvalue(r, seed=42)
    p2 = permutation_pvalue(r, seed=42)
    assert p1 == p2


def test_insufficient_data() -> None:
    """Less than 5 trades returns 1.0."""
    p = permutation_pvalue([1.0, 2.0])
    assert p == 1.0


def test_pvalue_never_exactly_zero() -> None:
    """A11: a perfect edge beating ALL permutations yields (0+1)/(n+1), not 0.

    With all +2R the actual mean is the maximum possible sign-flip mean, so no
    permutation strictly exceeds it; the small-sample correction floors the
    p-value at 1/(n_permutations+1) instead of an invalid exact 0.
    """
    r = [2.0] * 30
    n_perm = 1000
    p = permutation_pvalue(r, n_permutations=n_perm, seed=42)
    assert p == 1 / (n_perm + 1)
    assert p > 0.0
