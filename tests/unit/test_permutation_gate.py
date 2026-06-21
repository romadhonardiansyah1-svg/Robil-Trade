"""W7: Tests for permutation p-value gate in validation."""

from rtrade.backtest.metrics import BacktestMetrics
from rtrade.backtest.validation import run_validation_gates


def _make_metrics(**overrides: object) -> BacktestMetrics:
    defaults = {
        "n_trades": 200,
        "win_rate": 0.55,
        "expectancy": 0.5,
        "profit_factor": 1.6,
        "sharpe_ratio": 1.2,
        "sharpe_per_trade": 0.3,
        "max_drawdown_pct": 15.0,
        "avg_win_r": 2.0,
        "avg_loss_r": 1.0,
        "total_return_pct": 30.0,
        "skewness": 0.1,
        "kurtosis": 3.1,
    }
    defaults.update(overrides)
    return BacktestMetrics(**defaults)  # type: ignore[arg-type]


def test_permutation_pass() -> None:
    """p=0.01 should pass the gate."""
    m = _make_metrics()
    result = run_validation_gates(m, permutation_p=0.01)
    assert result.permutation_p == 0.01
    assert result.gate_results["permutation_p <= 0.05"] is True


def test_permutation_fail() -> None:
    """p=0.2 should fail the gate and all_passed=False."""
    m = _make_metrics()
    result = run_validation_gates(m, permutation_p=0.2)
    assert result.gate_results["permutation_p <= 0.05"] is False
    assert result.all_passed is False


def test_permutation_none_no_gate() -> None:
    """When permutation_p is None, the gate is not added."""
    m = _make_metrics()
    result = run_validation_gates(m, permutation_p=None)
    assert result.permutation_p is None
    assert "permutation_p <= 0.05" not in result.gate_results
