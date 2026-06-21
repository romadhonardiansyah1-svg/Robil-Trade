"""Backtest metrics — expectancy, profit factor, Sharpe, max DD, WR (PLAN §8.11)."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

# A13: when there are no losing trades the ratio gross_profit / gross_loss is
# mathematically +inf. Returning a non-finite value lets Gate 3 (profit_factor)
# pass trivially on a handful of lucky trades and breaks JSON serialisation /
# downstream comparisons. We cap it at a large FINITE sentinel instead, so a
# zero-loss sample is treated as "very good but bounded".
PROFIT_FACTOR_CAP = 1000.0


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    """Computed metrics from a backtest run."""

    n_trades: int
    win_rate: float
    expectancy: float  # average R-multiple
    profit_factor: float  # gross profit / gross loss (capped, see PROFIT_FACTOR_CAP)
    sharpe_ratio: float  # annualised by ACTUAL trade frequency (A3)
    sharpe_per_trade: float  # raw per-trade Sharpe = mean(r)/std(r, ddof=1) (A3)
    max_drawdown_pct: float
    avg_win_r: float
    avg_loss_r: float
    total_return_pct: float
    skewness: float
    kurtosis: float  # EXCESS kurtosis (normal == 0.0)


def compute_metrics(
    r_multiples: list[float],
    equity_curve: list[float],
    *,
    trades_per_year: float | None = None,
) -> BacktestMetrics:
    """Compute all backtest metrics from R-multiples and equity curve.

    A3 (Sharpe annualization): R-multiples are *per-trade* returns, so we first
    compute the raw per-trade Sharpe ``sharpe_per_trade = mean(r)/std(r, ddof=1)``
    and then annualize by the ACTUAL trade frequency:
    ``sharpe_ratio = sharpe_per_trade * sqrt(trades_per_year)``.

    ``trades_per_year`` should be supplied by callers that know the real
    out-of-sample span as ``n_trades / (oos_span_days / 365.25)``. When it is
    ``None`` we FALL BACK to ``len(r_multiples)`` — i.e. we assume the sample
    spans roughly one year. This fallback is deliberately conservative for
    reporting only; it is NOT used by the validation gate, which consumes the
    un-annualized ``sharpe_per_trade`` directly (see validation.deflated_sharpe_ratio).
    """
    n = len(r_multiples)
    if n == 0:
        return BacktestMetrics(
            n_trades=0,
            win_rate=0,
            expectancy=0,
            profit_factor=0,
            sharpe_ratio=0,
            sharpe_per_trade=0,
            max_drawdown_pct=0,
            avg_win_r=0,
            avg_loss_r=0,
            total_return_pct=0,
            skewness=0,
            kurtosis=0,
        )

    # A12: a 0R trade is a "scratch" — neither a win nor a loss. Wins are
    # strictly positive, losses strictly negative; scratch trades are excluded
    # from win_rate, avg_loss and the profit factor. win_rate is therefore the
    # fraction of *decisive* (non-scratch) trades that won.
    wins = [r for r in r_multiples if r > 0]
    losses = [r for r in r_multiples if r < 0]

    n_decisive = len(wins) + len(losses)
    win_rate = len(wins) / n_decisive if n_decisive > 0 else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    expectancy = sum(r_multiples) / n

    gross_profit = sum(wins) if wins else 0
    gross_loss = abs(sum(losses)) if losses else 0
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    else:
        # A13: no losing trades -> finite sentinel cap (not +inf).
        profit_factor = PROFIT_FACTOR_CAP if gross_profit > 0 else 0.0

    # Sharpe: raw per-trade first, then annualise by ACTUAL trade frequency (A3).
    arr = np.array(r_multiples)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1)) if n > 1 else 0.0
    sharpe_per_trade = (mean_r / std_r) if std_r > 0 else 0.0
    tpy = trades_per_year if trades_per_year is not None else float(n)
    sharpe = sharpe_per_trade * math.sqrt(tpy) if tpy > 0 else 0.0

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
        sharpe_per_trade=sharpe_per_trade,
        max_drawdown_pct=max_dd,
        avg_win_r=avg_win,
        avg_loss_r=avg_loss,
        total_return_pct=total_return,
        skewness=skewness,
        kurtosis=kurtosis,
    )


def trades_per_year_from_span(n_trades: int, span_days: float) -> float | None:
    """Derive trade frequency for Sharpe annualization (A3).

    ``trades_per_year = n_trades / (span_days / 365.25)``. Returns ``None`` when
    it cannot be derived (no trades or non-positive span) so ``compute_metrics``
    falls back to its documented default.
    """
    if n_trades <= 0 or span_days <= 0:
        return None
    return n_trades / (span_days / 365.25)


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
