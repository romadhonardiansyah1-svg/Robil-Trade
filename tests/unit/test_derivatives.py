"""T20: Tests for derivatives funding extreme detection."""

from rtrade.data.derivatives import is_funding_extreme


def test_extreme_positive() -> None:
    assert is_funding_extreme(0.0006) is True


def test_extreme_negative() -> None:
    assert is_funding_extreme(-0.0006) is True


def test_not_extreme() -> None:
    assert is_funding_extreme(0.0001) is False


def test_boundary() -> None:
    assert is_funding_extreme(0.0005) is True


def test_zero() -> None:
    assert is_funding_extreme(0.0) is False
