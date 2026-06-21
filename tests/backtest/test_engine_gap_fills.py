"""A9: pessimistic gap-aware SL/TP fills.

When a bar OPENS beyond the level:
- STOP LOSS (stop order) slips with the gap → fill at the WORSE of stop and open
  (BUY: min(stop, open); SELL: max(stop, open)).
- TAKE PROFIT (limit order) does NOT improve on a gap → fill at exactly the TP level.

Applies to both the non-smart exit path and the smart-exit path.
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


class TestNonSmartGapFills:
    def test_buy_sl_gap_down_fills_at_open(self) -> None:
        """BUY: exit bar gaps DOWN through the stop → fill at the open, not stop."""
        df = _make_df(
            [
                (100.0, 100.0, 100.0, 100.0),  # 0: signal
                (100.0, 101.0, 99.0, 100.0),  # 1: fill @ 100
                (90.0, 92.0, 88.0, 89.0),  # 2: gaps down through SL=95
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
        trade = run_backtest(df, signals).trades[0]
        assert trade.fill_bar == 1
        assert trade.exit_reason == "SL"
        assert trade.exit_price == pytest.approx(90.0)  # gapped open, worse than stop
        assert trade.exit_price < 95.0

    def test_sell_sl_gap_up_fills_at_open(self) -> None:
        """SELL: exit bar gaps UP through the stop → fill at the open, not stop."""
        df = _make_df(
            [
                (100.0, 100.0, 100.0, 100.0),  # 0: signal
                (100.0, 101.0, 99.0, 100.0),  # 1: fill @ 100
                (110.0, 112.0, 109.0, 111.0),  # 2: gaps up through SL=105
            ]
        )
        signals = [
            {
                "bar_index": 0,
                "direction": "SELL",
                "entry_limit": 100.0,
                "stop_loss": 105.0,
                "take_profit": 90.0,
                "valid_bars": 3,
            }
        ]
        trade = run_backtest(df, signals).trades[0]
        assert trade.fill_bar == 1
        assert trade.exit_reason == "SL"
        assert trade.exit_price == pytest.approx(110.0)  # gapped open, worse than stop
        assert trade.exit_price > 105.0

    def test_buy_tp_gap_up_fills_at_tp_not_open(self) -> None:
        """BUY: exit bar gaps UP through TP → fill at exactly TP (no improvement)."""
        df = _make_df(
            [
                (100.0, 100.0, 100.0, 100.0),  # 0: signal
                (100.0, 101.0, 99.0, 100.0),  # 1: fill @ 100
                (115.0, 116.0, 114.0, 115.0),  # 2: gaps up through TP=110
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
        trade = run_backtest(df, signals).trades[0]
        assert trade.fill_bar == 1
        assert trade.exit_reason == "TP"
        assert trade.exit_price == pytest.approx(110.0)  # limit order: no gap improvement


class TestSmartGapFills:
    def test_buy_smart_sl_gap_down_fills_at_open(self) -> None:
        """Smart path BUY: gap-down stop bar exits at the gapped open, not current_sl."""
        df = _make_df(
            [
                (100.0, 100.0, 100.0, 100.0),  # 0: signal
                (100.0, 100.5, 99.5, 100.0),  # 1: fill @ 100
                (90.0, 92.0, 88.0, 89.0),  # 2: gaps down through SL=95
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
        # Disable partial/BE/trailing so current_sl stays at the original stop (95).
        cfg = SmartExitConfig(
            partial_tp_enabled=False, breakeven_enabled=False, trailing_enabled=False
        )
        trade = run_backtest(df, signals, smart_exit=cfg).trades[0]
        assert trade.fill_bar == 1
        assert trade.exit_reason == "SL"
        assert trade.exit_price == pytest.approx(90.0)  # gapped open, worse than current_sl
        assert trade.exit_price < 95.0
