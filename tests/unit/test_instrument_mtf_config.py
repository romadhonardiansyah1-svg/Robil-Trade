from __future__ import annotations

from pydantic import ValidationError
import pytest

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe


def _inst(**over: object) -> InstrumentConfig:
    base: dict[str, object] = {
        "symbol": "XAUUSD",
        "market": Market.METALS,
        "provider": "oanda",
        "provider_symbol": "XAU_USD",
        "timeframes": [Timeframe.M5, Timeframe.M15, Timeframe.H4],
        "context_timeframe": Timeframe.D1,
        "pip_size": 0.01,
        "quote_currency": "USD",
    }
    base.update(over)
    return InstrumentConfig(**base)  # type: ignore[arg-type]


def test_defaults_are_back_compat() -> None:
    inst = _inst(timeframes=[Timeframe.H1, Timeframe.H4])
    assert inst.entry_timeframes == []
    assert inst.anchor_timeframe is None
    assert inst.resolved_entry_timeframes() == [Timeframe.H1]
    assert inst.resolved_anchor_timeframe() == Timeframe.H4


def test_configured_mtf_resolves_to_configured_values() -> None:
    inst = _inst(
        entry_timeframes=[Timeframe.M5, Timeframe.M15],
        anchor_timeframe=Timeframe.H4,
    )
    assert inst.resolved_entry_timeframes() == [Timeframe.M5, Timeframe.M15]
    assert inst.resolved_anchor_timeframe() == Timeframe.H4


def test_entry_tf_must_be_in_timeframes() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.H1], anchor_timeframe=Timeframe.H4)


def test_anchor_tf_must_be_in_timeframes() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.M5], anchor_timeframe=Timeframe.D1)


def test_anchor_cannot_also_be_entry() -> None:
    with pytest.raises(ValidationError):
        _inst(
            entry_timeframes=[Timeframe.M5, Timeframe.H4],
            anchor_timeframe=Timeframe.H4,
        )


def test_duplicate_entry_tfs_rejected() -> None:
    with pytest.raises(ValidationError):
        _inst(entry_timeframes=[Timeframe.M5, Timeframe.M5], anchor_timeframe=Timeframe.H4)
