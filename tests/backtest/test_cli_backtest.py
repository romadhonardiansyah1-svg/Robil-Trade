"""P1-6 (G-04): go-live backtest gate runner CLI.

Deterministic coverage of the runnable pieces:
- ``parse_gate_expr`` — turns the settings.yaml gate strings (">= 0.90") into
  numeric thresholds.
- ``_evaluate`` — runs the validation gates with thresholds pulled from config,
  so changing a threshold in settings.yaml flips pass/fail with NO code change
  (the core of QA-BT-01's "thresholds_from_config" acceptance).
- the ``backtest`` subcommand is wired into the unified CLI dispatcher.

The end-to-end DB-backed walk-forward run (seeded candles -> exit 0/1, row
persisted) is QA-BT-01 and needs a live seeded Postgres; it is an integration
test and is skipped in this environment.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rtrade.backtest.metrics import BacktestMetrics
from rtrade.cli.__main__ import _COMMANDS
from rtrade.cli.backtest import _evaluate, parse_gate_expr
from rtrade.core.config import AppConfig

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (">= 0.90", 0.90),
        ("> 0", 0.0),
        (">= 1.15", 1.15),
        ("0.30", 0.30),
        (0.25, 0.25),
        (25, 25.0),
        ("<= -1.5", -1.5),
    ],
)
def test_parse_gate_expr(raw: str | float, expected: float) -> None:
    assert parse_gate_expr(raw) == pytest.approx(expected)


def test_parse_gate_expr_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_gate_expr("no number here")


def _passing_metrics() -> BacktestMetrics:
    """Metrics that clear every default gate (strong, positive edge)."""
    return BacktestMetrics(
        n_trades=150,
        win_rate=0.55,
        expectancy=0.25,
        profit_factor=1.5,
        sharpe_ratio=2.0,
        max_drawdown_pct=10.0,
        avg_win_r=1.2,
        avg_loss_r=-0.8,
        total_return_pct=30.0,
        skewness=0.0,
        kurtosis=0.0,
    )


def _cfg() -> AppConfig:
    return AppConfig.load(config_dir=_CONFIG_DIR, env_file=None)


def test_evaluate_passes_strong_metrics() -> None:
    vgr = _evaluate(_cfg(), _passing_metrics(), permutation_p=0.01)
    assert vgr.all_passed is True


def test_config_threshold_flips_result_without_code_change() -> None:
    cfg = _cfg()
    cfg.settings.backtest.gates.oos_profit_factor = ">= 2.0"
    vgr = _evaluate(cfg, _passing_metrics(), permutation_p=0.01)
    assert vgr.all_passed is False
    assert vgr.gate_results["profit_factor >= 1.15"] is False


def test_min_trades_floor_fails_below_threshold() -> None:
    vgr = _evaluate(_cfg(), replace(_passing_metrics(), n_trades=50), permutation_p=0.01)
    assert vgr.all_passed is False
    assert vgr.gate_results["n_trades_oos >= 100"] is False


def test_backtest_subcommand_registered() -> None:
    assert "backtest" in _COMMANDS
