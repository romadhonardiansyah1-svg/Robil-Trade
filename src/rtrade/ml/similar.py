"""T29: Case-based memory — find similar historical setups via k-NN.

Computes Euclidean distance on normalized confluence breakdown features
to find the k most similar historical trades and their outcomes.

Hour-of-day is cyclic-encoded as (sin, cos) of the 24h angle so that
adjacent hours across the midnight boundary (e.g. 23 and 0) are close,
rather than maximally distant under a naive linear hour/23 scaling.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt

# Feature normalization ranges (from confluence breakdown max scores).
# NOTE: `hour` is intentionally absent here — it is cyclic-encoded separately
# into two (sin, cos) components which are already in [-1, 1] and need no
# range normalization.
_FEATURE_RANGES: dict[str, float] = {
    "trend": 25.0,
    "momentum": 20.0,
    "structure": 20.0,
    "volume": 15.0,
    "macro": 20.0,
}


def _cyclic_hour(hour: float) -> tuple[float, float]:
    """Encode hour-of-day (0-23) as (sin, cos) of its 24h angle."""
    angle = 2.0 * math.pi * hour / 24.0
    return math.sin(angle), math.cos(angle)


def _feature_vector(values: dict[str, Any], hour: float) -> npt.NDArray[np.float64]:
    """Build the normalized + cyclic-hour feature vector for one setup."""
    vec = [float(values.get(f, 0.0)) / _FEATURE_RANGES[f] for f in _FEATURE_RANGES]
    sin_h, cos_h = _cyclic_hour(hour)
    vec.extend((sin_h, cos_h))
    return np.array(vec, dtype=float)


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

    # Normalize current features (cyclic hour from top-level).
    curr_vec = _feature_vector(current, float(current.get("hour", 0.0)))

    # Compute distances.
    distances: list[tuple[float, dict[str, Any]]] = []
    for h in history:
        breakdown = h.get("confluence_breakdown", {})
        h_vec = _feature_vector(breakdown, float(breakdown.get("hour", 0.0)))
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
