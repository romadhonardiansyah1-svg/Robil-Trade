from __future__ import annotations

from rtrade.core.constants import Timeframe
from rtrade.pipeline.scan import _warmup_deficit_mtf


def test_entry_under_warmup_reports_entry_first() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=100,
        entry_tf=Timeframe.M5,
        bars_anchor=100,
        anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out == {"timeframe": "5m", "bars": 100, "required": 500}


def test_anchor_under_warmup_when_entry_ok() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=600,
        entry_tf=Timeframe.M15,
        bars_anchor=120,
        anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out == {"timeframe": "4h", "bars": 120, "required": 500}


def test_fully_warmed_returns_none() -> None:
    out = _warmup_deficit_mtf(
        bars_entry=600,
        entry_tf=Timeframe.M5,
        bars_anchor=600,
        anchor_tf=Timeframe.H4,
        warmup_bars=500,
    )
    assert out is None
