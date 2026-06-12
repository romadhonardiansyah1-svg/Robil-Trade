"""T26: Tests for Bayesian Kelly fraction."""

from rtrade.risk.kelly import bayesian_kelly_fraction


def test_positive_edge() -> None:
    """60 wins / 40 losses, avg_win 2R, avg_loss 1R → positive fraction."""
    f = bayesian_kelly_fraction(60, 40, 2.0, 1.0)
    assert f is not None
    assert 0 < f < 0.5  # quarter-Kelly should be well under 0.5


def test_insufficient_data() -> None:
    """n < 30 → None."""
    f = bayesian_kelly_fraction(5, 3, 2.0, 1.0)
    assert f is None


def test_negative_edge() -> None:
    """10 wins / 90 losses → None (negative Kelly)."""
    f = bayesian_kelly_fraction(10, 90, 2.0, 1.0)
    assert f is None


def test_deterministic() -> None:
    """Same input produces same output."""
    f1 = bayesian_kelly_fraction(60, 40, 2.0, 1.0)
    f2 = bayesian_kelly_fraction(60, 40, 2.0, 1.0)
    assert f1 == f2
