"""Tests that the live signal path routes sizing through the hardened
`compute_position_size` (backlog #1 / B3-B4 production coverage).

The defect: `generate_candidate` step 8 did INLINE sizing, so the LIVE path
never inherited the min-lot abstain (B3) or the GR-05 cap. These tests prove the
routing: an over-risking min-lot case ABSTAINS (returns None), a normal case
produces a candidate whose size matches `compute_position_size`, and a
misconfigured risk_pct > 2.0 abstains (returns None) instead of crashing.
"""

from __future__ import annotations

import pandas as pd

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Action, Market, Regime, Timeframe
from rtrade.risk.sizing import compute_position_size
from rtrade.signals.engine import generate_candidate
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, Strategy, StrategyConfig


class _FakeStrategy(Strategy):
    """Minimal deterministic strategy that emits a fixed BUY setup."""

    def __init__(self, levels: LevelSet) -> None:
        self._levels = levels

    @property
    def name(self) -> str:
        return "fake_sizing"

    @property
    def required_regime(self) -> Regime:
        return Regime.TREND

    def populate_indicators(self, df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
        return df

    def entry_signal(self, df: pd.DataFrame) -> EntryIntent | None:
        return EntryIntent(action=Action.BUY, reason="test setup")

    def custom_entry_price(self, df: pd.DataFrame, intent: EntryIntent) -> LevelSet:
        return self._levels


def _instrument() -> InstrumentConfig:
    return InstrumentConfig(
        symbol="XAUUSD",
        market=Market.METALS,
        provider="twelvedata",
        provider_symbol="XAU/USD",
        timeframes=[Timeframe.H1],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-06-30", periods=3, freq="1h", tz="UTC")
    return pd.DataFrame(
        {"close": [10_000.0, 10_000.0, 10_000.0], "atr": [400.0, 400.0, 400.0]}, index=idx
    )


# entry=10000, sl=9000 -> sl_dist=1000; tp=11500 -> RR=1.5 (>=1.5);
# atr=400 -> sl_dist/atr = 2.5 (in [0.5, 3.0]).
_LEVELS = LevelSet(
    entry_limit=10_000.0, stop_loss=9_000.0, take_profit=11_500.0, atr_at_signal=400.0
)

# sl_dist=1100 chosen so 100/1100 = 0.0909090909... differs between the OLD 4dp
# inline rounding (0.0909) and the hardened 8dp value (0.09090909) — proving the
# routing actually switched paths, not just coincidentally matched.
_LEVELS_PRECISE = LevelSet(
    entry_limit=10_000.0, stop_loss=8_900.0, take_profit=11_650.0, atr_at_signal=400.0
)


def _generate(**overrides: object) -> object:
    kwargs: dict[str, object] = {
        "strategy": _FakeStrategy(_LEVELS),
        "strategy_cfg": StrategyConfig(raw={}),
        "instrument": _instrument(),
        "df_1h": _df(),
        "df_4h": None,
        "sr_levels": [],
        "gap_zones": [],
        "confluence_min_score": 0,
        "edge_quality_enabled": False,
    }
    kwargs.update(overrides)
    return generate_candidate(**kwargs)  # type: ignore[arg-type]


def test_min_lot_over_risk_abstains_returns_none() -> None:
    """B3: a lot_step that floors the size to zero would over-risk on one lot,
    so the LIVE path must ABSTAIN (return None), not emit an over-risking candidate.

    equity=100, risk_pct=1.0 -> budget $1; sl_dist=1000 -> 0.001 lots;
    floor(0.001/0.01)*0.01 = 0.0 -> abstain.
    """
    result = _generate(equity=100.0, risk_pct=1.0, lot_step=0.01)
    assert result is None


def test_normal_case_routes_through_hardened_sizing() -> None:
    """Routing proof: a valid case produces a candidate whose position_size
    equals compute_position_size(...).position_size (lot_step=None). The chosen
    sl_dist makes the hardened 8dp value differ from the OLD 4dp inline value."""
    expected = compute_position_size(
        equity=10_000.0, risk_pct=1.0, sl_distance=1100.0, pip_size=0.01, lot_step=None
    )
    candidate = _generate(strategy=_FakeStrategy(_LEVELS_PRECISE), equity=10_000.0, risk_pct=1.0)
    assert candidate is not None
    assert candidate.position_size == expected.position_size


def test_risk_pct_over_cap_abstains_no_crash() -> None:
    """GR-05: risk_pct > 2.0 makes compute_position_size raise; the live path
    must guard it -> return None (abstain), never crash the scan."""
    result = _generate(equity=10_000.0, risk_pct=3.0)
    assert result is None
