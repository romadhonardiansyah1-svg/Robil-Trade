"""Backtest metrics — expectancy, profit factor, Sharpe, max DD, WR (PLAN §8.11)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Computed metrics from a backtest run."""

    n_trades: int
    win_rate: float
    expectancy: float  # average R-multiple
    profit_factor: float  # gross profit / gross loss
    sharpe_ratio: float  # annualised
    max_drawdown_pct: float
    avg_win_r: float
    avg_loss_r: float
    total_return_pct: float
    skewness: float
    kurtosis: float


def compute_metrics(
    r_multiples: list[float],
    equity_curve: list[float],
    *,
    periods_per_year: float = 252.0,  # for Sharpe annualisation
) -> BacktestMetrics:
    """Compute all backtest metrics from R-multiples and equity curve."""
    n = len(r_multiples)
    if n == 0:
        return BacktestMetrics(
            n_trades=0,
            win_rate=0,
            expectancy=0,
            profit_factor=0,
            sharpe_ratio=0,
            max_drawdown_pct=0,
            avg_win_r=0,
            avg_loss_r=0,
            total_return_pct=0,
            skewness=0,
            kurtosis=0,
        )

    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r <= 0]

    win_rate = len(wins) / n if n > 0 else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    expectancy = sum(r_multiples) / n

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe ratio (annualised from per-trade returns).
    arr = np.array(r_multiples)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    sharpe = (mean_r / std_r * math.sqrt(periods_per_year)) if std_r > 0 else 0.0

    # Max drawdown from equity curve.
    eq = np.array(equity_curve)
    if len(eq) > 0:
        peak = np.maximum.accumulate(eq)
        drawdown = (peak - eq) / peak * 100
        max_dd = float(np.max(drawdown))
    else:
        max_dd = 0.0

    # Total return.
    if len(equity_curve) >= 2 and equity_curve[0] > 0:
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0] * 100
    else:
        total_return = 0.0

    # Higher moments for DSR.
    skewness = float(np.nan_to_num(_skewness(arr)))
    kurtosis = float(np.nan_to_num(_kurtosis(arr)))

    return BacktestMetrics(
        n_trades=n,
        win_rate=win_rate,
        expectancy=expectancy,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        max_drawdown_pct=max_dd,
        avg_win_r=avg_win,
        avg_loss_r=avg_loss,
        total_return_pct=total_return,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def _skewness(arr: np.ndarray) -> float:  # type: ignore[type-arg]
    """Sample skewness."""
    n = len(arr)
    if n < 3:
        return 0.0
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    return float(n / ((n - 1) * (n - 2)) * np.sum(((arr - mean) / std) ** 3))


def _kurtosis(arr: np.ndarray) -> float:  # type: ignore[type-arg]
    """Excess kurtosis."""
    n = len(arr)
    if n < 4:
        return 0.0
    mean = np.mean(arr)
    std = np.std(arr, ddof=1)
    if std == 0:
        return 0.0
    m4 = np.mean((arr - mean) ** 4)
    return float(m4 / std**4 - 3)
