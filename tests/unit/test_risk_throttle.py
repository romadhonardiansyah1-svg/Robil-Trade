"""T27: Tests for equity-curve risk throttle."""

from rtrade.risk.limits import throttled_risk_pct


def test_throttle_negative_expectancy() -> None:
    """10 outcomes averaging -0.2 → risk 1.0 * 0.5 = 0.5."""
    outcomes = [-0.2] * 10
    r = throttled_risk_pct(1.0, outcomes, window=10, mult=0.5)
    assert r == 0.5


def test_no_throttle_positive() -> None:
    """Positive expectancy → unchanged."""
    outcomes = [0.5] * 10
    r = throttled_risk_pct(1.0, outcomes, window=10, mult=0.5)
    assert r == 1.0


def test_no_throttle_insufficient_data() -> None:
    """< window trades → unchanged."""
    outcomes = [-1.0] * 5
    r = throttled_risk_pct(1.0, outcomes, window=10, mult=0.5)
    assert r == 1.0


def test_never_exceeds_base() -> None:
    """Result never exceeds base_risk_pct."""
    outcomes = [5.0] * 10
    r = throttled_risk_pct(1.0, outcomes, window=10, mult=0.5)
    assert r <= 1.0
