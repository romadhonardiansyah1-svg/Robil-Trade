"""Transaction cost models (PLAN §8.11.2, config/costs.yaml).

Conservative estimates. Backtest WITHOUT costs is PROHIBITED as basis for
any decision (PLAN §8.11.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class CostModel:
    """Transaction cost model for one instrument."""

    symbol: str
    # Percentage-based costs (round-turn).
    spread_pct_rt: float = 0.0  # spread as % of price (round-turn)
    commission_pct_rt: float = 0.0  # commission as % of price
    slippage_pct_per_side: float = 0.0  # slippage per side
    # Pip-based costs (for forex).
    spread_pips_rt: float = 0.0
    commission_usd_per_lot_rt: float = 0.0
    slippage_pips_per_side: float = 0.0
    # Crypto-specific.
    taker_fee_pct_per_side: float = 0.0

    @property
    def total_pct_rt(self) -> float:
        """Total cost as % of price (round-turn)."""
        pct = self.spread_pct_rt + self.commission_pct_rt
        pct += self.slippage_pct_per_side * 2  # both sides
        pct += self.taker_fee_pct_per_side * 2
        return pct


def compute_trade_cost(model: CostModel, entry_price: float, direction: str) -> float:
    """Compute total cost in price units for one trade (round-turn).

    Returns the cost as a price differential (to subtract from PnL).
    """
    # Percentage-based.
    pct_cost = entry_price * (model.total_pct_rt / 100)

    # Pip-based (forex — approximate conversion).
    pip_cost = model.spread_pips_rt * 0.0001  # assuming 4-decimal pair
    pip_cost += model.slippage_pips_per_side * 2 * 0.0001

    return pct_cost + pip_cost


def load_cost_models(config_path: Path | str = Path("config/costs.yaml")) -> dict[str, CostModel]:
    """Load cost models from YAML config."""
    path = Path(config_path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    costs = data.get("costs", {})
    models: dict[str, CostModel] = {}

    for symbol, params in costs.items():
        models[symbol] = CostModel(
            symbol=symbol,
            spread_pct_rt=float(params.get("spread_pct_round_turn", 0)),
            commission_pct_rt=float(params.get("commission_pct_round_turn", 0)),
            slippage_pct_per_side=float(params.get("slippage_pct_per_side", 0)),
            spread_pips_rt=float(params.get("spread_pips_round_turn", 0)),
            commission_usd_per_lot_rt=float(params.get("commission_usd_per_lot_round_turn", 0)),
            slippage_pips_per_side=float(params.get("slippage_pips_per_side", 0)),
            taker_fee_pct_per_side=float(params.get("taker_fee_pct_per_side", 0)),
        )

    return models
