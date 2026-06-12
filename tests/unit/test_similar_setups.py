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
