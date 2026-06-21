"""Validation-gate tests — meaningful Deflated Sharpe Ratio (A4).

Reference: Bailey & López de Prado (2014), "The Deflated Sharpe Ratio".
All Sharpe quantities here are in PER-TRADE units (no annualization), matching
the DSR derivation (SR estimate, its standard error, and E[max] are all
per-trade / dimensionless).
"""

from __future__ import annotations

from itertools import pairwise

import numpy as np

from rtrade.backtest.metrics import BacktestMetrics
from rtrade.backtest.validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probability_of_backtest_overfitting,
    run_validation_gates,
)


def _metrics(**overrides: object) -> BacktestMetrics:
    defaults: dict[str, object] = {
        "n_trades": 120,
        "win_rate": 0.5,
        "expectancy": 0.05,
        "profit_factor": 1.2,
        "sharpe_ratio": 1.0,
        "sharpe_per_trade": 0.1,
        "max_drawdown_pct": 10.0,
        "avg_win_r": 1.0,
        "avg_loss_r": 1.0,
        "total_return_pct": 5.0,
        "skewness": 0.0,
        "kurtosis": 0.0,
    }
    defaults.update(overrides)
    return BacktestMetrics(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# expected_max_sharpe — dimensionless, depends ONLY on n_trials
# --------------------------------------------------------------------------- #
def test_expected_max_sharpe_zero_for_single_trial() -> None:
    assert expected_max_sharpe(1) == 0.0
    assert expected_max_sharpe(0) == 0.0


def test_expected_max_sharpe_strictly_increasing_in_n() -> None:
    ns = [2, 5, 10, 50, 100]
    vals = [expected_max_sharpe(n) for n in ns]
    for earlier, later in pairwise(vals):
        assert later > earlier


# --------------------------------------------------------------------------- #
# deflated_sharpe_ratio — meaningful, per-trade units
# --------------------------------------------------------------------------- #
def test_dsr_strong_strategy_high_probability() -> None:
    """Strong per-trade edge with few trials should clear 0.90."""
    dsr = deflated_sharpe_ratio(0.5, n_trials=2, t_periods=500, skewness=0.0, kurtosis=0.0)
    assert dsr > 0.90


def test_dsr_mediocre_strategy_below_threshold() -> None:
    """Mediocre per-trade edge with many trials must NOT rubber-stamp ~1.0."""
    dsr = deflated_sharpe_ratio(0.1, n_trials=50, t_periods=120, skewness=0.0, kurtosis=0.0)
    assert dsr < 0.90


def test_dsr_degenerate_denominator_returns_zero() -> None:
    """A non-positive variance term means we cannot certify -> fail safe (0.0)."""
    # denom = 1 - g3*SR + (ek+2)/4*SR^2 = 1 - 5*0.5 + 0.5*0.25 = -1.375 <= 0
    dsr = deflated_sharpe_ratio(0.5, n_trials=2, t_periods=100, skewness=5.0, kurtosis=0.0)
    assert dsr == 0.0


# --------------------------------------------------------------------------- #
# run_validation_gates — uses per-trade Sharpe + undeflated flag
# --------------------------------------------------------------------------- #
def test_gate_uses_per_trade_sharpe_not_annualized() -> None:
    """A mediocre per-trade edge under many trials fails Gate 5 (not a rubber stamp)."""
    m = _metrics(sharpe_per_trade=0.1, sharpe_ratio=1.1, n_trades=120)
    res = run_validation_gates(m, n_trials=50, permutation_p=None)
    assert res.dsr_probability < 0.90
    assert res.gate_results["dsr_prob >= 0.90"] is False


def test_gate_passes_strong_per_trade_sharpe() -> None:
    m = _metrics(sharpe_per_trade=0.8, sharpe_ratio=10.0, n_trades=500)
    res = run_validation_gates(m, n_trials=2, permutation_p=None)
    assert res.dsr_probability > 0.90


def test_dsr_undeflated_flag_when_single_config() -> None:
    res = run_validation_gates(_metrics(), n_trials=1, permutation_p=None)
    assert res.dsr_undeflated is True


def test_dsr_deflated_flag_when_multiple_configs() -> None:
    res = run_validation_gates(_metrics(), n_trials=10, permutation_p=None)
    assert res.dsr_undeflated is False


# --------------------------------------------------------------------------- #
# PBO as a model-SELECTION diagnostic (A5, Option A) — NOT a single-config gate
# --------------------------------------------------------------------------- #
def test_pbo_gate_absent_when_not_evaluated() -> None:
    """Single-config run (pbo_value=None): no PBO gate, no rubber stamp."""
    res = run_validation_gates(_metrics(), pbo_value=None, permutation_p=None)
    assert "pbo <= 0.30" not in res.gate_results
    assert res.pbo is None


def test_pbo_gate_present_and_fails_when_overfit() -> None:
    """A supplied PBO above max_pbo adds the gate and fails it."""
    res = run_validation_gates(_metrics(), pbo_value=0.5, max_pbo=0.30, permutation_p=None)
    assert res.pbo == 0.5
    assert res.gate_results["pbo <= 0.30"] is False
    assert res.all_passed is False


def test_pbo_gate_present_and_passes_when_low() -> None:
    """A supplied low PBO adds the gate and passes it."""
    res = run_validation_gates(_metrics(), pbo_value=0.1, max_pbo=0.30, permutation_p=None)
    assert res.pbo == 0.1
    assert res.gate_results["pbo <= 0.30"] is True


# --------------------------------------------------------------------------- #
# probability_of_backtest_overfitting — FAIL CLOSED on insufficient data
# --------------------------------------------------------------------------- #
def test_pbo_insufficient_rows_fails_closed() -> None:
    """T < s_partitions must return 1.0 (degenerate == fully overfit)."""
    matrix = np.zeros((4, 1), dtype=float)
    assert probability_of_backtest_overfitting(matrix, s_partitions=16) == 1.0


def test_pbo_single_config_fails_closed() -> None:
    """N < 2 (a single configuration) must return 1.0, not 0.0."""
    matrix = np.zeros((20, 1), dtype=float)
    assert probability_of_backtest_overfitting(matrix, s_partitions=16) == 1.0
