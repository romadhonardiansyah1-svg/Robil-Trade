from __future__ import annotations

from pathlib import Path

import pytest

from rtrade.backtest.costs import CostModel, load_cost_models

_COSTS = Path(__file__).resolve().parents[2] / "config" / "costs.yaml"


def test_xauusd_cost_model_present_and_nonzero() -> None:
    models = load_cost_models(_COSTS)
    assert "XAUUSD" in models, "XAUUSD must have a cost entry so gold scalps are costed"
    xau = models["XAUUSD"]
    assert isinstance(xau, CostModel)
    # Pinned scalping-realistic values (config/costs.yaml).
    assert xau.spread_pct_rt == 0.010
    assert xau.commission_pct_rt == 0.007
    assert xau.slippage_pct_per_side == 0.010
    assert xau.pip_size == 0.01


def test_xauusd_round_turn_cost_is_applied_not_zero() -> None:
    xau = load_cost_models(_COSTS)["XAUUSD"]
    # spread + commission + 2×slippage = 0.010 + 0.007 + 0.020 = 0.037 %RT.
    assert xau.total_pct_rt == pytest.approx(0.037)
    assert xau.total_pct_rt > 0.0
