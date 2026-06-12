"""T29: Case-based memory — find similar historical setups via k-NN.

Computes Euclidean distance on normalized confluence breakdown features
to find the k most similar historical trades and their outcomes.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Feature normalization ranges (from confluence breakdown max scores).
_FEATURE_RANGES: dict[str, float] = {
    "trend": 25.0,
    "momentum": 20.0,
    "structure": 20.0,
    "volume": 15.0,
    "macro": 20.0,
    "hour": 23.0,
}


def find_similar_setups(
    current: dict[str, float],
    history: list[dict[str, Any]],
    k: int = 12,
) -> dict[str, Any]:
    """Find k most similar historical setups by Euclidean distance.

    Args:
        current: Current signal's confluence breakdown features.
        history: List of historical signal dicts with confluence breakdown
            and outcome_r fields.
        k: Number of neighbors.

    Returns:
        {"n": k, "wins": x, "losses": y, "win_rate": float, "avg_r": float}
        or {"n": 0} if history < 30.
    """
    if len(history) < 30:
        return {"n": 0}

    features = list(_FEATURE_RANGES.keys())

    # Normalize current features.
    curr_vec = np.array([current.get(f, 0.0) / _FEATURE_RANGES[f] for f in features], dtype=float)

    # Compute distances.
    distances: list[tuple[float, dict[str, Any]]] = []
    for h in history:
        breakdown = h.get("confluence_breakdown", {})
        h_vec = np.array(
            [breakdown.get(f, 0.0) / _FEATURE_RANGES[f] for f in features],
            dtype=float,
        )
        dist = float(np.linalg.norm(curr_vec - h_vec))
        distances.append((dist, h))

    distances.sort(key=lambda x: x[0])
    neighbors = [h for _, h in distances[:k]]

    outcomes = [h["outcome_r"] for h in neighbors if h.get("outcome_r") is not None]
    if not outcomes:
        return {"n": 0}

    wins = sum(1 for r in outcomes if r > 0)
    losses = len(outcomes) - wins

    return {
        "n": len(outcomes),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(outcomes), 4) if outcomes else 0.0,
        "avg_r": round(sum(outcomes) / len(outcomes), 4),
    }
