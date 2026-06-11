"""Walk-forward harness (PLAN §8.11.3).

Rolling: train 12 months → test 3 months → step 3 months over ≥3 years data.
Optimisation of parameters ONLY on train window. Results = concatenation of
all OOS test windows (pure out-of-sample).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from rtrade.backtest.costs import CostModel
from rtrade.backtest.engine import run_backtest
from rtrade.backtest.metrics import BacktestMetrics, compute_metrics


@dataclass
class WalkForwardWindow:
    """One train-test window in the walk-forward process."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_metrics: BacktestMetrics | None = None
    test_metrics: BacktestMetrics | None = None
    test_trades: list[dict[str, object]] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """Complete walk-forward analysis result."""

    windows: list[WalkForwardWindow]
    oos_metrics: BacktestMetrics  # concatenated OOS results
    oos_r_multiples: list[float]
    oos_equity_curve: list[float]
    n_trials: int  # total parameter combinations tested (for DSR)


def generate_windows(
    start_date: datetime,
    end_date: datetime,
    *,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
) -> list[WalkForwardWindow]:
    """Generate rolling train/test windows."""
    windows: list[WalkForwardWindow] = []
    current = start_date

    while True:
        train_start = current
        train_end = _add_months(train_start, train_months)
        test_start = train_end
        test_end = _add_months(test_start, test_months)

        if test_end > end_date:
            break

        windows.append(
            WalkForwardWindow(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

        current = _add_months(current, step_months)

    return windows


def _add_months(dt: datetime, months: int) -> datetime:
    """Add months to a datetime (approximate)."""
    month = dt.month + months
    year = dt.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(dt.day, 28)  # safe for all months
    return dt.replace(year=year, month=month, day=day)


def run_walk_forward(
    df: pd.DataFrame,
    signal_generator: object,
    *,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
    initial_equity: float = 10_000.0,
    risk_pct: float = 1.0,
    cost_model: CostModel | None = None,
    n_trials: int = 1,
) -> WalkForwardResult:
    """Run walk-forward analysis.

    This is a simplified version that expects signals to be pre-generated.
    The signal_generator is a callable that takes a DataFrame and returns
    a list of signal dicts.

    For the full walk-forward with parameter optimization, extend this
    to iterate over a parameter grid on the train set.
    """
    if df.empty:
        raise ValueError("empty DataFrame for walk-forward")

    start_date = pd.Timestamp(df.index[0]).to_pydatetime()
    end_date = pd.Timestamp(df.index[-1]).to_pydatetime()

    windows = generate_windows(
        start_date,
        end_date,
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
    )

    all_oos_r: list[float] = []
    all_oos_equity: list[float] = [initial_equity]
    equity = initial_equity

    for window in windows:
        # Split data.
        test_df = df[
            (df.index >= pd.Timestamp(window.test_start))
            & (df.index < pd.Timestamp(window.test_end))
        ]

        if test_df.empty:
            continue

        # Generate signals on test period using fixed params from train.
        # (In full implementation, optimize params on train set first.)
        if callable(signal_generator):
            test_signals = signal_generator(test_df)  # type: ignore[operator]
        else:
            test_signals = []

        if not test_signals:
            continue

        # Run backtest on test period.
        result = run_backtest(
            test_df,
            test_signals,
            initial_equity=equity,
            risk_pct=risk_pct,
            cost_model=cost_model,
        )

        # Collect OOS results.
        test_r = [t.r_multiple for t in result.trades if t.r_multiple is not None]
        all_oos_r.extend(test_r)
        equity = result.final_equity
        all_oos_equity.extend(result.equity_curve[1:])

        # Store window metrics.
        window.test_metrics = compute_metrics(test_r, result.equity_curve)

    # Compute aggregate OOS metrics.
    oos_metrics = compute_metrics(all_oos_r, all_oos_equity)

    return WalkForwardResult(
        windows=windows,
        oos_metrics=oos_metrics,
        oos_r_multiples=all_oos_r,
        oos_equity_curve=all_oos_equity,
        n_trials=n_trials,
    )
