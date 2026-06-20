from __future__ import annotations

import pandas as pd
import pytest

from rtrade.core.constants import Action, Regime
from rtrade.signals.schemas import LevelSet
from rtrade.strategies.base import EntryIntent, StrategyConfig
from rtrade.strategies.s3_mtf_scalper import S3MtfScalper


def _rising_frame(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series([100.0 + 0.7 * i for i in range(n)])
    return pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]).to_numpy(),
            "high": (close + 0.6).to_numpy(),
            "low": (close - 0.6).to_numpy(),
            "close": close.to_numpy(),
            "volume": [1000.0] * n,
            "rsi": [55.0] * n,
            "atr": [1.2] * n,
        },
        index=idx,
    )


def _flat_frame(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="5min", tz="UTC")
    close = pd.Series([100.0] * n)
    return pd.DataFrame(
        {
            "open": close.to_numpy(),
            "high": (close + 0.5).to_numpy(),
            "low": (close - 0.5).to_numpy(),
            "close": close.to_numpy(),
            "volume": [1000.0] * n,
            "rsi": [50.0] * n,
            "atr": [1.0] * n,
        },
        index=idx,
    )


def _long_setup() -> tuple[S3MtfScalper, pd.DataFrame]:
    strat = S3MtfScalper()
    df = strat.populate_indicators(_rising_frame(), StrategyConfig(raw={}))
    ema20 = float(df["s3_ema_fast"].iloc[-1])
    ema50 = float(df["s3_ema_mid"].iloc[-1])
    assert ema20 > ema50  # uptrend sanity before crafting the trigger bar
    mid = (ema20 + ema50) / 2.0  # between EMA50 and EMA20 -> touch value, hold structure
    df.iloc[-1, df.columns.get_loc("low")] = mid
    df.iloc[-1, df.columns.get_loc("close")] = ema20 + 3.0  # reclaim above EMA20 (+ above VWAP)
    df.iloc[-1, df.columns.get_loc("high")] = ema20 + 3.5
    df.iloc[-1, df.columns.get_loc("rsi")] = 52.0
    df.iloc[-2, df.columns.get_loc("rsi")] = 42.0  # dip then turning up
    return strat, df


def test_metadata() -> None:
    strat = S3MtfScalper()
    assert strat.name == "s3_mtf_scalper"
    assert strat.required_regime == Regime.TREND


def test_long_pullback_setup_emits_buy() -> None:
    strat, df = _long_setup()
    intent = strat.entry_signal(df)
    assert isinstance(intent, EntryIntent)
    assert intent.action == Action.BUY


def test_flat_market_emits_nothing() -> None:
    strat = S3MtfScalper()
    df = strat.populate_indicators(_flat_frame(), StrategyConfig(raw={}))
    assert strat.entry_signal(df) is None


def test_custom_entry_price_levels_long() -> None:
    strat, df = _long_setup()
    intent = strat.entry_signal(df)
    assert intent is not None
    levels = strat.custom_entry_price(df, intent)
    assert isinstance(levels, LevelSet)
    assert levels.stop_loss < levels.entry_limit < levels.take_profit
    rr = (levels.take_profit - levels.entry_limit) / (levels.entry_limit - levels.stop_loss)
    assert rr == pytest.approx(1.8, abs=1e-6)
    atr_mult = (levels.entry_limit - levels.stop_loss) / levels.atr_at_signal
    assert 0.5 <= atr_mult <= 3.0


def test_registered_in_registry() -> None:
    from rtrade.strategies import STRATEGY_REGISTRY

    assert STRATEGY_REGISTRY["s3_mtf_scalper"] is S3MtfScalper
