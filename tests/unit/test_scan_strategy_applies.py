from __future__ import annotations

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
import rtrade.pipeline.scan as scan_mod
from rtrade.strategies import StrategyConfig


def _inst(symbol: str = "XAUUSD") -> InstrumentConfig:
    return InstrumentConfig(
        symbol=symbol,
        market=Market.METALS if symbol == "XAUUSD" else Market.FOREX,
        provider="oanda",
        provider_symbol="XAU_USD",
        timeframes=[Timeframe.M5, Timeframe.M15, Timeframe.H4],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


_SCALP = StrategyConfig(raw={"instruments": ["XAUUSD"], "entry_timeframes": ["5m", "15m"]})


def test_scalper_applies_on_xauusd_entry_tf() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.M5) is True
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.M15) is True


def test_scalper_skipped_on_other_symbol() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("EURUSD"), Timeframe.M5) is False


def test_scalper_skipped_on_non_entry_tf() -> None:
    assert scan_mod._strategy_applies(_SCALP, _inst("XAUUSD"), Timeframe.H1) is False


def test_no_allowlist_always_applies() -> None:
    swing = StrategyConfig(raw={})
    assert scan_mod._strategy_applies(swing, _inst("EURUSD"), Timeframe.H1) is True
    assert scan_mod._strategy_applies(swing, _inst("XAUUSD"), Timeframe.M5) is True


def test_real_swing_configs_excluded_from_scalping_timeframes() -> None:
    """C2 regression: the real S1/S2 YAML configs carry entry_timeframes:[1h] so
    they never run on XAUUSD M5/M15 (owned by S3/S4) and never preempt them."""
    inst = _inst("XAUUSD")
    for name in ("s1_trend_pullback", "s2_range_mr"):
        cfg = scan_mod._load_strategy_config(name)
        assert cfg.get("entry_timeframes") == ["1h"], f"{name} must pin entry_timeframes"
        assert scan_mod._strategy_applies(cfg, inst, Timeframe.M5) is False
        assert scan_mod._strategy_applies(cfg, inst, Timeframe.M15) is False
        assert scan_mod._strategy_applies(cfg, inst, Timeframe.H1) is True
