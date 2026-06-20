"""Cost model tests for A6 (per-lot commission) and A7 (refuse cost-free).

A6: ``commission_usd_per_lot_rt`` must be converted to a per-unit price cost via
``commission_usd_per_lot_rt / contract_size`` and added to the round-turn cost.
A7: ``get_cost_model`` must refuse unconfigured symbols unless explicitly allowed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rtrade.backtest.costs import (
    CostModel,
    compute_trade_cost,
    get_cost_model,
    load_cost_models,
)
from rtrade.core.errors import ConfigError

_COSTS = Path(__file__).resolve().parents[2] / "config" / "costs.yaml"


class TestPerLotCommission:
    def test_eurusd_cost_includes_per_lot_commission(self) -> None:
        eurusd = load_cost_models(_COSTS)["EURUSD"]
        # Per-lot commission must be loaded and charged.
        assert eurusd.commission_usd_per_lot_rt == pytest.approx(7.0)
        assert eurusd.contract_size == pytest.approx(100_000.0)

        cost = compute_trade_cost(eurusd, 1.1000, "BUY")

        # Pip-only cost (the term that existed before A6).
        pip_only = (
            eurusd.spread_pips_rt * eurusd.pip_size
            + eurusd.slippage_pips_per_side * 2 * eurusd.pip_size
        )
        commission_price_per_unit = eurusd.commission_usd_per_lot_rt / eurusd.contract_size
        assert commission_price_per_unit == pytest.approx(0.00007)
        assert cost > pip_only
        assert cost == pytest.approx(pip_only + commission_price_per_unit)

    def test_zero_contract_size_disables_per_lot_commission(self) -> None:
        model = CostModel(
            symbol="X", commission_usd_per_lot_rt=7.0, contract_size=0.0, pip_size=0.0001
        )
        # Guard against div-by-zero: no per-lot commission term.
        assert compute_trade_cost(model, 1.0, "BUY") == pytest.approx(0.0)


class TestGetCostModel:
    def test_missing_symbol_raises(self) -> None:
        with pytest.raises(ConfigError, match="GBPUSD"):
            get_cost_model("GBPUSD", config_path=_COSTS)

    def test_missing_symbol_allow_missing_returns_none(self) -> None:
        assert get_cost_model("GBPUSD", config_path=_COSTS, allow_missing=True) is None

    def test_configured_symbol_returns_model(self) -> None:
        model = get_cost_model("EURUSD", config_path=_COSTS)
        assert isinstance(model, CostModel)
        assert model.symbol == "EURUSD"
