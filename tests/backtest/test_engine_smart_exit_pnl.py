"""A1: engine must apply smart-exit realized P&L (partials + remaining leg).

A BUY that takes a 50% partial at +1R and is then stopped at breakeven should
report total R ≈ +0.5 (realized 0.5R from the closed partial leg + 0.5 remaining
× 0R at the breakeven exit), NOT 0R from a full-position fill→exit computation.
"""

from __future__ import annotations

import pandas as pd
import pytest

from rtrade.backtest.engine import run_backtest
from rtrade.backtest.smart_exit import SmartExitConfig


def _make_df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a UTC-indexed OHLC DataFrame from (open, high, low, close) rows."""
    index = pd.date_range("2024-01-01", periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        },
        index=index,
    )


def test_partial_then_breakeven_reports_half_r() -> None:
    # Bar 0: signal bar. Bar 1: fill at 100. Bar 2: +1R (partial 50% + BE→100).
    # Bar 3: returns to entry and stops at breakeven (100).
    df = _make_df(
        [
            (100.0, 100.0, 100.0, 100.0),  # 0: signal
            (100.0, 101.0, 99.0, 100.0),  # 1: fill @ 100
            (100.0, 105.0, 100.5, 104.0),  # 2: +1R, partial + BE to 100
            (100.0, 100.0, 99.0, 100.0),  # 3: drops back, stopped at BE (100)
        ]
    )
    signals = [
        {
            "bar_index": 0,
            "direction": "BUY",
            "entry_limit": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "valid_bars": 3,
        }
    ]

    result = run_backtest(df, signals, smart_exit=SmartExitConfig())

    trade = result.trades[0]
    assert trade.fill_bar == 1
    assert trade.exit_reason == "SL"
    assert trade.exit_price == 100.0  # breakeven stop
    assert trade.r_multiple is not None
    # realized 0.5R (partial) + 0.5 remaining * 0R (BE exit) = +0.5R.
    assert trade.r_multiple == pytest.approx(0.5)


def test_full_position_tp_unchanged() -> None:
    # A clean +2R TP with no partial taken before TP must still report ~2R,
    # ensuring the realized-leg accounting does not distort full-position trades.
    df = _make_df(
        [
            (100.0, 100.0, 100.0, 100.0),  # 0: signal
            (100.0, 100.5, 99.5, 100.0),  # 1: fill @ 100
            (100.0, 111.0, 100.5, 110.5),  # 2: blasts through TP=110
        ]
    )
    signals = [
        {
            "bar_index": 0,
            "direction": "BUY",
            "entry_limit": 100.0,
            "stop_loss": 95.0,
            "take_profit": 110.0,
            "valid_bars": 3,
        }
    ]

    result = run_backtest(
        df,
        signals,
        smart_exit=SmartExitConfig(
            partial_tp_enabled=False, breakeven_enabled=False, trailing_enabled=False
        ),
    )

    trade = result.trades[0]
    assert trade.exit_reason == "TP"
    assert trade.r_multiple == pytest.approx(2.0)
