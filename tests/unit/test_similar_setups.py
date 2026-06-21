"""T29: Tests for case-based memory (similar setups)."""

from rtrade.ml.similar import find_similar_setups


def _make_history(n: int, win: bool = True) -> list[dict]:
    """Create n synthetic history entries."""
    base = {"trend": 20, "momentum": 15, "structure": 15, "volume": 10, "macro": 15, "hour": 12}
    return [
        {
            "confluence_breakdown": base,
            "outcome_r": 2.0 if win else -1.0,
        }
        for _ in range(n)
    ]


def test_similar_wins() -> None:
    """50 similar-win histories → high win_rate."""
    current = {"trend": 20, "momentum": 15, "structure": 15, "volume": 10, "macro": 15, "hour": 12}
    history = _make_history(30, win=True) + _make_history(20, win=False)
    # Make losers different
    for h in history[30:]:
        h["confluence_breakdown"] = {
            "trend": 5,
            "momentum": 5,
            "structure": 5,
            "volume": 5,
            "macro": 5,
            "hour": 5,
        }
    result = find_similar_setups(current, history, k=12)
    assert result["n"] == 12
    assert result["win_rate"] > 0.7


def test_insufficient_history() -> None:
    """< 30 histories → n=0."""
    current = {"trend": 20, "momentum": 15, "structure": 15, "volume": 10, "macro": 15, "hour": 12}
    result = find_similar_setups(current, _make_history(10))
    assert result["n"] == 0


def test_cyclic_hour_wraps_midnight_boundary() -> None:
    """G2: hour 23 is adjacent in time to hour 0, so a current setup at hour 23
    must be nearer to hour-0 history than to hour-12 history (all else equal).

    With a linear hour/23 encoding hour 23 is closest to hour 12 -> selects the
    losers; with cyclic (sin, cos) encoding it selects the hour-0 winners.
    """
    base = {"trend": 20, "momentum": 15, "structure": 15, "volume": 10, "macro": 15}
    current = {**base, "hour": 23}

    winners_at_midnight = [
        {"confluence_breakdown": {**base, "hour": 0}, "outcome_r": 2.0} for _ in range(15)
    ]
    losers_at_noon = [
        {"confluence_breakdown": {**base, "hour": 12}, "outcome_r": -1.0} for _ in range(15)
    ]
    history = winners_at_midnight + losers_at_noon

    result = find_similar_setups(current, history, k=12)

    assert result["n"] == 12
    # All nearest neighbours are the hour-0 winners.
    assert result["win_rate"] == 1.0
    assert result["avg_r"] == 2.0
