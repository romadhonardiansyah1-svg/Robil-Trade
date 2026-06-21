"""Market structure analysis — swing points, S/R levels, gaps (PLAN §8.2).

Pure functions, no I/O. Operates on DataFrames with OHLC columns.

- Swing High/Low: fractal 2-left-2-right pattern.
- S/R levels: clustering of swing points with tolerance 0.25×ATR.
- Gap/inefficiency: measured gap between high[i-2] and low[i] > 0.5×ATR.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """A significant high or low detected by fractal analysis."""

    index: int  # position in DataFrame
    price: float
    is_high: bool  # True = swing high, False = swing low
    ts: pd.Timestamp


@dataclass(frozen=True, slots=True)
class SRLevel:
    """Support or resistance level derived from clustered swing points."""

    price: float
    strength: int  # number of swing points in cluster
    is_resistance: bool
    touches: list[pd.Timestamp]


@dataclass(frozen=True, slots=True)
class GapZone:
    """Measured gap / fair value gap / inefficiency zone."""

    high: float  # upper bound
    low: float  # lower bound
    direction: str  # "bullish" or "bearish"
    bar_index: int
    ts: pd.Timestamp


def detect_swing_points(
    df: pd.DataFrame,
    *,
    left: int = 2,
    right: int = 2,
) -> list[SwingPoint]:
    """Detect swing highs and lows using a fractal (N-left, N-right) pattern.

    A swing high at bar i: high[i] equals the max of high[i-left : i+right+1],
    i is the leftmost bar holding that max, and high[i] strictly exceeds at
    least one adjacent bar. Equal highs (double tops / liquidity pools) are
    detected, but a flat top yields exactly one swing high. Symmetric for lows.
    """
    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    index = df.index

    points: list[SwingPoint] = []

    # Equal-extreme rule (F7): we deliberately allow equal highs/lows so that
    # double tops/bottoms and equal-high/low liquidity pools (which SMC relies
    # on) are detected. To avoid marking every bar of a flat top, a bar i is a
    # swing high only when it is BOTH the window max AND the LEFTMOST bar in the
    # window holding that max (np.argmax returns the first occurrence), AND it
    # strictly exceeds at least one immediately adjacent bar (so a perfectly
    # flat interior is not a swing). A flat top therefore yields exactly ONE
    # swing high (its leftmost bar). The rule is symmetric for swing lows and is
    # fully deterministic.
    for i in range(left, len(df) - right):
        # Swing high.
        window_h = highs[i - left : i + right + 1]
        leftmost_max = int(np.argmax(window_h)) == left
        if (
            highs[i] == np.max(window_h)
            and leftmost_max
            and (highs[i] > highs[i - 1] or highs[i] > highs[i + 1])
        ):
            points.append(
                SwingPoint(
                    index=i,
                    price=float(highs[i]),
                    is_high=True,
                    ts=pd.Timestamp(index[i]),
                )
            )

        # Swing low.
        window_l = lows[i - left : i + right + 1]
        leftmost_min = int(np.argmin(window_l)) == left
        if (
            lows[i] == np.min(window_l)
            and leftmost_min
            and (lows[i] < lows[i - 1] or lows[i] < lows[i + 1])
        ):
            points.append(
                SwingPoint(
                    index=i,
                    price=float(lows[i]),
                    is_high=False,
                    ts=pd.Timestamp(index[i]),
                )
            )

    return points


def cluster_sr_levels(
    swing_points: list[SwingPoint],
    atr: float,
    *,
    tolerance_atr_mult: float = 0.25,
    min_touches: int = 2,
) -> list[SRLevel]:
    """Cluster swing points into S/R levels (PLAN §8.2).

    Points within `tolerance_atr_mult × ATR` of each other are grouped.
    Clusters with fewer than `min_touches` are discarded.
    """
    if not swing_points or atr <= 0:
        return []

    tolerance = tolerance_atr_mult * atr
    # Sort by price (the single, deterministic ordering this algorithm uses).
    sorted_points = sorted(swing_points, key=lambda p: p.price)

    # Single-linkage clustering (F6): start a NEW cluster whenever the gap to the
    # PREVIOUS price-sorted point exceeds `tolerance`. Comparing to the previous
    # point (rather than the cluster's running mean, which drifts as points are
    # added) makes the result deterministic and order-independent: shuffling the
    # input produces identical levels, and a cluster can never grow wider than
    # the sum of sub-tolerance gaps between its members.
    clusters: list[list[SwingPoint]] = []
    current_cluster: list[SwingPoint] = [sorted_points[0]]

    for point in sorted_points[1:]:
        prev_point = current_cluster[-1]
        if point.price - prev_point.price > tolerance:
            clusters.append(current_cluster)
            current_cluster = [point]
        else:
            current_cluster.append(point)
    clusters.append(current_cluster)

    # Build SRLevel from clusters.
    levels: list[SRLevel] = []
    for cluster in clusters:
        if len(cluster) < min_touches:
            continue
        avg_price = sum(p.price for p in cluster) / len(cluster)
        # Resistance vs support by majority touch type. On an exact tie (equal
        # number of highs and lows touching the level) classify as resistance
        # deterministically — an explicit, documented choice rather than relying
        # on the implicit `>` float comparison defaulting to support.
        highs_count = sum(1 for p in cluster if p.is_high)
        lows_count = len(cluster) - highs_count
        is_resistance = highs_count >= lows_count
        levels.append(
            SRLevel(
                price=avg_price,
                strength=len(cluster),
                is_resistance=is_resistance,
                touches=[p.ts for p in cluster],
            )
        )

    return sorted(levels, key=lambda l: l.price)


def detect_gaps(
    df: pd.DataFrame,
    atr: float,
    *,
    min_gap_atr_mult: float = 0.5,
) -> list[GapZone]:
    """Detect measured gap/inefficiency zones (PLAN §8.2).

    A bullish gap: low[i] > high[i-2] (price jumped up, leaving a gap).
    A bearish gap: high[i] < low[i-2] (price dropped, leaving a gap).
    Only gaps > min_gap_atr_mult × ATR are reported.
    """
    if len(df) < 3 or atr <= 0:
        return []

    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values
    index = df.index
    threshold = min_gap_atr_mult * atr

    gaps: list[GapZone] = []
    for i in range(2, len(df)):
        # Bullish gap: low of current > high of 2 bars ago.
        bullish_gap = lows[i] - highs[i - 2]
        if bullish_gap > threshold:
            gaps.append(
                GapZone(
                    high=float(lows[i]),
                    low=float(highs[i - 2]),
                    direction="bullish",
                    bar_index=i,
                    ts=pd.Timestamp(index[i]),
                )
            )

        # Bearish gap: high of current < low of 2 bars ago.
        bearish_gap = lows[i - 2] - highs[i]
        if bearish_gap > threshold:
            gaps.append(
                GapZone(
                    high=float(lows[i - 2]),
                    low=float(highs[i]),
                    direction="bearish",
                    bar_index=i,
                    ts=pd.Timestamp(index[i]),
                )
            )

    return gaps
