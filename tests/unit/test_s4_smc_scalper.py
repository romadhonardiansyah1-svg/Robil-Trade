from __future__ import annotations

import pandas as pd
import pytest

from rtrade.core.constants import Action, Regime
from rtrade.indicators.smc import liquidity_sweeps, market_structure, order_blocks
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, StrategyConfig
from rtrade.strategies.s4_smc_scalper import S4SmcScalper

OHLC = tuple[float, float, float, float]

# 15 bars (swing_lookback=2): low-side sweep of 100 @9, bullish BOS @13,
# bullish order block @12 (down candle before the break). Hand-traced against
# the SP-3 detector definitions.
_BULL_ROWS: list[OHLC] = [
    (100.0, 104.0, 99.0, 103.0),
    (103.0, 106.0, 102.0, 105.0),
    (105.0, 108.0, 104.0, 107.0),
    (107.0, 110.0, 106.0, 108.0),  # swing high 110 @3
    (108.0, 109.0, 105.0, 106.0),
    (106.0, 108.0, 103.0, 104.0),
    (104.0, 106.0, 100.0, 101.0),  # swing low 100 @6
    (101.0, 105.0, 102.0, 103.0),
    (103.0, 106.0, 104.0, 105.0),
    (105.0, 107.0, 98.0, 102.0),  # sweep: low 98 < 100, close 102 > 100 @9
    (102.0, 106.0, 101.0, 104.0),
    (104.0, 107.0, 103.0, 106.0),
    (106.0, 108.0, 105.0, 105.0),  # down candle -> order block @12
    (105.0, 112.0, 104.0, 111.0),  # close 111 > 110 -> bullish BOS @13
    (111.0, 113.0, 110.0, 112.0),
]


def _df(rows: list[OHLC], *, atr: float = 3.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1000.0] * len(rows),
            "atr": [atr] * len(rows),
        },
        index=idx,
    )


def _cfg() -> StrategyConfig:
    return StrategyConfig(raw={"smc": {"swing_lookback": 2}, "min_bars": 10})


def test_metadata() -> None:
    strat = S4SmcScalper()
    assert strat.name == "s4_smc_scalper"
    assert strat.required_regime == Regime.TREND


def test_detector_fixture_sanity() -> None:
    # Guard the hand-traced fixture against detector drift.
    df = _df(_BULL_ROWS)
    sweeps = liquidity_sweeps(df, swing_lookback=2)
    events = market_structure(df, swing_lookback=2)
    blocks = order_blocks(df, swing_lookback=2)
    assert any(s.side == "low" and s.idx == 9 for s in sweeps)
    assert any(e.direction == "bullish" and e.idx == 13 for e in events)
    assert any(b.direction == "bullish" and b.idx == 12 for b in blocks)


def test_bull_setup_emits_buy() -> None:
    strat = S4SmcScalper()
    df = strat.populate_indicators(_df(_BULL_ROWS), _cfg())
    intent = strat.entry_signal(df)
    assert isinstance(intent, EntryIntent)
    assert intent.action == Action.BUY


def test_flat_market_emits_nothing() -> None:
    strat = S4SmcScalper()
    flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 15
    df = strat.populate_indicators(_df(flat), _cfg())
    assert strat.entry_signal(df) is None


def test_custom_entry_price_levels_long() -> None:
    strat = S4SmcScalper()
    df = strat.populate_indicators(_df(_BULL_ROWS), _cfg())
    intent = strat.entry_signal(df)
    assert intent is not None
    levels = strat.custom_entry_price(df, intent)
    assert isinstance(levels, LevelSet)
    # entry at OB top (108), SL beyond swept level (100) - 0.25*ATR(3) = 99.25.
    assert levels.entry_limit == pytest.approx(108.0)
    assert levels.stop_loss == pytest.approx(99.25)
    assert levels.stop_loss < levels.entry_limit < levels.take_profit
    rr = (levels.take_profit - levels.entry_limit) / (levels.entry_limit - levels.stop_loss)
    assert rr == pytest.approx(1.8, abs=1e-6)
    atr_mult = (levels.entry_limit - levels.stop_loss) / levels.atr_at_signal
    assert 0.5 <= atr_mult <= 3.0


def test_registered_in_registry() -> None:
    from rtrade.strategies import STRATEGY_REGISTRY

    assert STRATEGY_REGISTRY["s4_smc_scalper"] is S4SmcScalper
