"""Deterministic edge-quality filter for signal candidates.

This module rejects candidates that are technically valid but likely suffer
from adverse selection: excessive spread, volatility shock, rejection wicks,
dead liquidity, or trigger candles with poor participation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

import pandas as pd

from rtrade.core.constants import Action


@dataclass(frozen=True, slots=True)
class EdgeQualityConfig:
    """Thresholds for the deterministic edge-quality filter."""

    min_score: int = 65
    max_spread_atr: float = 0.12
    min_atr_percentile: float = 8.0
    max_atr_percentile: float = 96.0
    max_opposing_wick_ratio: float = 0.62
    max_total_wick_body_ratio: float = 6.0
    min_body_atr: float = 0.03
    min_volume_ratio: float = 0.55
    volume_window: int = 20
    max_range_expansion_atr: float = 2.8
    max_entry_distance_atr: float = 1.25


@dataclass(frozen=True, slots=True)
class EdgeQualityFailure:
    """One blocking edge-quality reason."""

    code: str
    reason: str


@dataclass(frozen=True, slots=True)
class EdgeQualityReport:
    """Result of the edge-quality assessment."""

    passed: bool
    score: int
    failures: tuple[EdgeQualityFailure, ...] = field(default_factory=tuple)
    metrics: dict[str, float] = field(default_factory=dict)


def assess_edge_quality(
    df: pd.DataFrame,
    action: Action,
    entry_limit: float,
    *,
    spread: float | None = None,
    config: EdgeQualityConfig | None = None,
) -> EdgeQualityReport:
    """Assess whether the latest closed bar is clean enough to trade.

    The filter is deliberately deterministic. It does not predict direction;
    it rejects bad execution environments and fragile trigger candles.
    """
    cfg = config or EdgeQualityConfig()
    failures: list[EdgeQualityFailure] = []

    if df.empty:
        return EdgeQualityReport(
            passed=False,
            score=0,
            failures=(EdgeQualityFailure("EQ-00", "empty dataframe"),),
        )

    last = df.iloc[-1]
    open_price = _as_float(last.get("open"))
    high = _as_float(last.get("high"))
    low = _as_float(last.get("low"))
    close = _as_float(last.get("close"))
    atr = _as_float(last.get("atr"))

    if min(open_price, high, low, close, atr, entry_limit) <= 0:
        return EdgeQualityReport(
            passed=False,
            score=0,
            failures=(EdgeQualityFailure("EQ-01", "invalid OHLC, ATR, or entry value"),),
        )

    true_range = max(high - low, atr * 0.01)
    body = abs(close - open_price)
    body_floor = max(body, atr * 0.01)
    upper_wick = max(0.0, high - max(open_price, close))
    lower_wick = max(0.0, min(open_price, close) - low)
    opposing_wick = upper_wick if action == Action.BUY else lower_wick

    atr_percentile = _as_float(last.get("atr_percentile"), default=50.0)
    spread_atr = None if spread is None else spread / atr
    volume_ratio = _volume_ratio(df, cfg.volume_window)

    metrics = {
        "atr": atr,
        "atr_percentile": atr_percentile,
        "body_atr": body / atr,
        "range_atr": true_range / atr,
        "opposing_wick_ratio": opposing_wick / true_range,
        "total_wick_body_ratio": (upper_wick + lower_wick) / body_floor,
        "entry_distance_atr": abs(close - entry_limit) / atr,
    }
    if spread_atr is not None:
        metrics["spread_atr"] = spread_atr
    if volume_ratio is not None:
        metrics["volume_ratio"] = volume_ratio

    score = 100

    if spread_atr is not None and spread_atr > cfg.max_spread_atr:
        score -= 25
        failures.append(
            EdgeQualityFailure(
                "EQ-02",
                f"spread/ATR {spread_atr:.3f} exceeds {cfg.max_spread_atr:.3f}",
            )
        )

    if atr_percentile < cfg.min_atr_percentile:
        score -= 20
        failures.append(
            EdgeQualityFailure(
                "EQ-03",
                f"ATR percentile {atr_percentile:.1f} below {cfg.min_atr_percentile:.1f}",
            )
        )
    elif atr_percentile > cfg.max_atr_percentile:
        score -= 25
        failures.append(
            EdgeQualityFailure(
                "EQ-04",
                f"ATR percentile {atr_percentile:.1f} above {cfg.max_atr_percentile:.1f}",
            )
        )

    opposing_wick_ratio = metrics["opposing_wick_ratio"]
    if opposing_wick_ratio > cfg.max_opposing_wick_ratio:
        score -= 20
        failures.append(
            EdgeQualityFailure(
                "EQ-05",
                (
                    f"opposing wick ratio {opposing_wick_ratio:.2f} exceeds "
                    f"{cfg.max_opposing_wick_ratio:.2f}"
                ),
            )
        )

    range_atr = metrics["range_atr"]
    if range_atr > cfg.max_range_expansion_atr:
        score -= 15
        failures.append(
            EdgeQualityFailure(
                "EQ-06",
                f"range/ATR {range_atr:.2f} exceeds {cfg.max_range_expansion_atr:.2f}",
            )
        )

    entry_distance_atr = metrics["entry_distance_atr"]
    if entry_distance_atr > cfg.max_entry_distance_atr:
        score -= 10
        failures.append(
            EdgeQualityFailure(
                "EQ-07",
                (
                    f"entry distance {entry_distance_atr:.2f} ATR exceeds "
                    f"{cfg.max_entry_distance_atr:.2f}"
                ),
            )
        )

    body_atr = metrics["body_atr"]
    if body_atr < cfg.min_body_atr:
        score -= 10

    total_wick_body_ratio = metrics["total_wick_body_ratio"]
    if total_wick_body_ratio > cfg.max_total_wick_body_ratio:
        score -= 10

    if volume_ratio is not None and volume_ratio < cfg.min_volume_ratio:
        score -= 10

    score = max(0, min(100, score))
    if score < cfg.min_score:
        failures.append(
            EdgeQualityFailure(
                "EQ-08",
                f"edge-quality score {score} below minimum {cfg.min_score}",
            )
        )

    return EdgeQualityReport(
        passed=score >= cfg.min_score and len(failures) == 0,
        score=score,
        failures=tuple(failures),
        metrics=metrics,
    )


def _as_float(value: object, *, default: float = 0.0) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not isfinite(result):
        return default
    return result


def _volume_ratio(df: pd.DataFrame, window: int) -> float | None:
    if "volume" not in df.columns:
        return None

    vol = df["volume"].astype(float)
    if vol.sum() <= 0:
        return None

    lookback = vol.tail(max(2, window))
    if len(lookback) < 2:
        return None

    baseline = float(lookback.iloc[:-1].median())
    latest = float(lookback.iloc[-1])
    if baseline <= 0:
        return None
    return latest / baseline
