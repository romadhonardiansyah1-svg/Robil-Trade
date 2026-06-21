"""Transaction cost models (PLAN §8.11.2, config/costs.yaml).

Conservative estimates. Backtest WITHOUT costs is PROHIBITED as basis for
any decision (PLAN §8.11.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from rtrade.core.errors import ConfigError


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
    # Instrument pip size.
    pip_size: float = 0.0001
    # Instrument units per 1 standard lot (forex: 100_000). Used to convert a
    # USD/lot round-turn commission into a per-unit price cost. Values <= 0
    # disable the per-lot commission term (guards against div-by-zero).
    contract_size: float = 100_000.0

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

    # Pip-based (forex — use instrument pip_size).
    pip_cost = model.spread_pips_rt * model.pip_size
    pip_cost += model.slippage_pips_per_side * 2 * model.pip_size

    # Per-lot commission (USD/lot RT) → per-unit price cost. The engine scales
    # this by position_size (instrument units), so dividing by contract_size
    # (units per lot) yields the correct USD charge. Guard div-by-zero.
    commission_cost = (
        (model.commission_usd_per_lot_rt / model.contract_size) if model.contract_size > 0 else 0.0
    )

    return pct_cost + pip_cost + commission_cost


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
            pip_size=float(params.get("pip_size", 0.0001)),
            contract_size=float(params.get("contract_size", 100_000.0)),
        )

    return models


def get_cost_model(
    symbol: str,
    *,
    config_path: Path | str = Path("config/costs.yaml"),
    allow_missing: bool = False,
) -> CostModel | None:
    """Return the cost model for ``symbol`` from ``config_path``.

    A backtest run cost-free is PROHIBITED as a decision basis (PLAN §8.11.2).
    If the symbol has no entry and ``allow_missing`` is False, raise
    :class:`ConfigError` naming the symbol and the config file. When
    ``allow_missing`` is True, return ``None`` for an unconfigured symbol.
    """
    models = load_cost_models(config_path)
    model = models.get(symbol)
    if model is None and not allow_missing:
        raise ConfigError(
            f"no cost model configured for {symbol!r} in {config_path}; "
            f"add an entry to costs.yaml or pass allow_missing/--allow-zero-cost "
            f"to run cost-free (NOT a valid decision basis)"
        )
    return model
