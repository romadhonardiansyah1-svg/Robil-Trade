"""Backtest harness: strategy → bar-by-bar signals → engine → metrics → gates (V1).

Pure functions that can be unit-tested without DB.
The CLI script (scripts/run_backtest.py) handles DB loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import structlog

from rtrade.backtest.costs import CostModel
from rtrade.backtest.engine import BacktestResult, run_backtest
from rtrade.backtest.metrics import BacktestMetrics, compute_metrics
from rtrade.backtest.permutation import permutation_pvalue
from rtrade.backtest.smart_exit import SmartExitConfig
from rtrade.backtest.validation import ValidationGateResult, run_validation_gates
from rtrade.backtest.walkforward import (
    WalkForwardWindow,
    generate_windows,
)
from rtrade.regime.rules import RegimeClassifier
from rtrade.strategies.base import Strategy, StrategyConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class HarnessResult:
    """Complete result of a single backtest run through the harness."""

    signals: list[dict[str, object]]
    backtest: BacktestResult
    metrics: BacktestMetrics
    gates: ValidationGateResult
    permutation_p: float


def generate_signals(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    df: pd.DataFrame,
    *,
    warmup_bars: int = 250,
    window_bars: int = 400,
    valid_bars: int = 6,
) -> list[dict[str, object]]:
    """Walk bar-by-bar; at each closed bar evaluate the strategy on a tail window.

    ANTI-LOOKAHEAD: indicators are computed ONCE on the full df — safe because all
    indicators are causal (EMA/RSI/ATR/ADX/rolling only look at the past).
    entry_signal()/custom_entry_price() only receive a slice df.iloc[:i+1]
    (tailed to window_bars for speed), so bar i never sees bar i+1.
    Regime is computed on-the-fly with a stateful classifier (hysteresis correct).
    """
    df = strategy.populate_indicators(df.copy(), strategy_cfg)
    classifier = RegimeClassifier()
    signals: list[dict[str, object]] = []

    for i in range(warmup_bars, len(df)):
        window = df.iloc[max(0, i + 1 - window_bars) : i + 1]
        regime = classifier.classify("BT", window)
        if regime.regime != strategy.required_regime:
            continue
        intent = strategy.entry_signal(window)
        if intent is None:
            continue
        try:
            levels = strategy.custom_entry_price(window, intent)
        except (ValueError, IndexError):
            continue
        if not strategy.confirm_signal(window, levels):
            continue
        # GR-03/04 minimal (mirror validate_and_round_levels sans pip rounding):
        sl_dist = abs(levels.entry_limit - levels.stop_loss)
        tp_dist = abs(levels.take_profit - levels.entry_limit)
        if sl_dist <= 0 or tp_dist / sl_dist < 1.5:
            continue
        atr_mult = sl_dist / levels.atr_at_signal if levels.atr_at_signal > 0 else 999
        if not (0.5 <= atr_mult <= 3.0):
            continue
        signals.append(
            {
                "bar_index": i,
                "direction": intent.action.value,
                "entry_limit": levels.entry_limit,
                "stop_loss": levels.stop_loss,
                "take_profit": levels.take_profit,
                "valid_bars": valid_bars,
            }
        )
    return signals


def run_harness(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    df: pd.DataFrame,
    *,
    cost_model: CostModel | None,
    smart_exit: SmartExitConfig | None = None,
    n_trials: int = 1,
) -> HarnessResult:
    """Full harness: generate signals → backtest → metrics → gates."""
    signals = generate_signals(strategy, strategy_cfg, df)
    bt = run_backtest(df, signals, cost_model=cost_model, smart_exit=smart_exit)
    r = [t.r_multiple for t in bt.trades if t.r_multiple is not None]
    metrics = compute_metrics(r, bt.equity_curve)
    perm_p = permutation_pvalue(r) if len(r) >= 5 else 1.0
    gates = run_validation_gates(metrics, n_trials, permutation_p=perm_p)
    return HarnessResult(
        signals=signals,
        backtest=bt,
        metrics=metrics,
        gates=gates,
        permutation_p=perm_p,
    )


# ---------------------------------------------------------------------------
# V2: Walk-forward harness
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardHarnessResult:
    """Walk-forward harness result with per-window and aggregate OOS metrics."""

    windows: list[WalkForwardWindow]
    oos_r_multiples: list[float]
    oos_metrics: BacktestMetrics
    oos_gates: ValidationGateResult
    permutation_p: float
    per_window_metrics: list[dict[str, Any]] = field(default_factory=list)


def run_walkforward_harness(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    df: pd.DataFrame,
    *,
    cost_model: CostModel | None,
    smart_exit: SmartExitConfig | None = None,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 3,
    warmup_bars: int = 250,
) -> WalkForwardHarnessResult:
    """Run walk-forward analysis with the harness signal generator.

    For each window:
    - Include the last `warmup_bars` bars of the train period in the df slice
      so indicators are warm, but discard any signals whose bar_index falls
      in the warmup zone.
    - Generate signals only on the test period bars.
    - Backtest those signals on the combined (warmup + test) slice.
    - Collect OOS R-multiples from all windows, then compute aggregate metrics + gates.
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
    per_window: list[dict[str, Any]] = []

    for window in windows:
        # Get test period slice (with warmup from train end).
        train_end_ts = pd.Timestamp(window.train_end)
        test_start_ts = pd.Timestamp(window.test_start)
        test_end_ts = pd.Timestamp(window.test_end)

        # Include warmup bars before test start for indicator computation.
        warmup_start = train_end_ts - pd.Timedelta(hours=warmup_bars)
        wf_df = df[(df.index >= warmup_start) & (df.index < test_end_ts)]

        if wf_df.empty or len(wf_df) < warmup_bars + 10:
            continue

        # Find the index boundary between warmup and test.
        test_mask = wf_df.index >= test_start_ts
        if not test_mask.any():
            continue
        first_test_iloc = int(test_mask.values.argmax())

        # Generate signals on the full wf_df (indicators warm from warmup).
        raw_signals = generate_signals(
            strategy, strategy_cfg, wf_df, warmup_bars=min(warmup_bars, first_test_iloc)
        )

        # Discard signals that fall in the warmup zone (before test start).
        test_signals = [s for s in raw_signals if int(str(s["bar_index"])) >= first_test_iloc]

        if not test_signals:
            per_window.append(
                {
                    "test_start": str(window.test_start.date()),
                    "test_end": str(window.test_end.date()),
                    "n_signals": 0,
                    "n_trades": 0,
                }
            )
            continue

        # Backtest on the wf_df slice.
        bt = run_backtest(wf_df, test_signals, cost_model=cost_model, smart_exit=smart_exit)
        window_r = [t.r_multiple for t in bt.trades if t.r_multiple is not None]
        all_oos_r.extend(window_r)

        window.test_metrics = compute_metrics(window_r, bt.equity_curve)
        per_window.append(
            {
                "test_start": str(window.test_start.date()),
                "test_end": str(window.test_end.date()),
                "n_signals": len(test_signals),
                "n_trades": window.test_metrics.n_trades,
                "expectancy": round(window.test_metrics.expectancy, 4),
                "win_rate": round(window.test_metrics.win_rate, 4),
            }
        )

    # Aggregate OOS metrics + gates.
    oos_equity = [10_000.0]
    eq = 10_000.0
    for r in all_oos_r:
        eq += eq * 0.01 * r  # 1% risk per trade
        oos_equity.append(eq)

    oos_metrics = compute_metrics(all_oos_r, oos_equity)
    perm_p = permutation_pvalue(all_oos_r) if len(all_oos_r) >= 5 else 1.0
    oos_gates = run_validation_gates(oos_metrics, permutation_p=perm_p)

    return WalkForwardHarnessResult(
        windows=windows,
        oos_r_multiples=all_oos_r,
        oos_metrics=oos_metrics,
        oos_gates=oos_gates,
        permutation_p=perm_p,
        per_window_metrics=per_window,
    )
