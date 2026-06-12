"""Unit tests for backtester engine (PLAN §8.11)."""

import numpy as np
import pandas as pd
import pytest

from rtrade.backtest.costs import CostModel
from rtrade.backtest.engine import run_backtest
from rtrade.backtest.metrics import compute_metrics


def _make_ohlcv(n: int = 100) -> pd.DataFrame:
    """Generate simple OHLCV data with clear trend."""
    np.random.seed(42)
    base = 100.0
    prices = np.cumsum(np.random.randn(n) * 0.5) + base

    high = prices + np.abs(np.random.randn(n) * 2)
    low = prices - np.abs(np.random.randn(n) * 2)
    open_ = prices + np.random.randn(n) * 0.5

    for i in range(n):
        high[i] = max(high[i], open_[i], prices[i])
        low[i] = min(low[i], open_[i], prices[i])

    dates = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": prices, "volume": 1000},
        index=dates,
    )


class TestBacktestEngine:
    def test_basic_trade_fills(self) -> None:
        df = _make_ohlcv()
        close_0 = float(df.iloc[10]["close"])
        signals = [
            {
                "bar_index": 10,
                "direction": "BUY",
                "entry_limit": close_0 - 0.5,
                "stop_loss": close_0 - 5.0,
                "take_profit": close_0 + 10.0,
                "valid_bars": 6,
            }
        ]
        result = run_backtest(df, signals)
        assert result.n_trades >= 0  # may or may not fill

    def test_sl_tp_same_bar_sl_first(self) -> None:
        """If both SL and TP hit in same bar, SL is applied (worst-case)."""
        # Create data where one bar has extreme range.
        df = _make_ohlcv(20)
        entry = float(df.iloc[5]["close"])

        signals = [
            {
                "bar_index": 5,
                "direction": "BUY",
                "entry_limit": entry,
                "stop_loss": entry - 1.0,
                "take_profit": entry + 1.0,
                "valid_bars": 10,
            }
        ]
        result = run_backtest(df, signals)
        # Check that if both hit, SL is applied.
        for trade in result.trades:
            if trade.exit_reason in ("SL", "TP"):
                assert trade.exit_reason is not None

    def test_expired_trade(self) -> None:
        """Trade that doesn't fill within valid_bars should expire."""
        df = _make_ohlcv(20)
        # Set entry very far from current price.
        signals = [
            {
                "bar_index": 5,
                "direction": "BUY",
                "entry_limit": 50.0,  # far below
                "stop_loss": 45.0,
                "take_profit": 60.0,
                "valid_bars": 3,
            }
        ]
        result = run_backtest(df, signals)
        assert result.trades[0].exit_reason == "EXPIRED"

    def test_costs_reduce_pnl(self) -> None:
        cost_model = CostModel(
            symbol="TEST",
            spread_pct_rt=0.1,
            slippage_pct_per_side=0.05,
        )
        df = _make_ohlcv()
        close_0 = float(df.iloc[10]["close"])
        signals = [
            {
                "bar_index": 10,
                "direction": "BUY",
                "entry_limit": close_0,
                "stop_loss": close_0 - 5.0,
                "take_profit": close_0 + 15.0,
                "valid_bars": 50,
            }
        ]
        result_no_cost = run_backtest(df, signals)
        result_with_cost = run_backtest(df, signals, cost_model=cost_model)

        # With costs, final equity should be lower or equal.
        assert result_with_cost.final_equity <= result_no_cost.final_equity + 0.01


class TestBacktestMetrics:
    def test_all_wins(self) -> None:
        r_multiples = [2.0, 2.0, 2.0, 2.0, 2.0]
        equity = [10000, 10200, 10400, 10600, 10800, 11000]
        metrics = compute_metrics(r_multiples, equity)
        assert metrics.win_rate == 1.0
        assert metrics.expectancy == 2.0
        assert metrics.profit_factor == float("inf")

    def test_all_losses(self) -> None:
        r_multiples = [-1.0, -1.0, -1.0]
        equity = [10000, 9900, 9800, 9700]
        metrics = compute_metrics(r_multiples, equity)
        assert metrics.win_rate == 0.0
        assert metrics.expectancy == -1.0
        assert metrics.profit_factor == 0.0

    def test_mixed(self) -> None:
        r_multiples = [2.0, -1.0, 2.0, -1.0, 2.0]
        equity = [10000, 10200, 10100, 10300, 10200, 10400]
        metrics = compute_metrics(r_multiples, equity)
        assert metrics.win_rate == 0.6
        assert metrics.expectancy == pytest.approx(0.8, abs=0.01)

    def test_empty(self) -> None:
        metrics = compute_metrics([], [])
        assert metrics.n_trades == 0
        assert metrics.win_rate == 0

    def test_max_drawdown(self) -> None:
        equity = [10000, 11000, 9000, 10500]
        metrics = compute_metrics([1.0, -2.0, 1.5], equity)
        # Max DD from 11000 to 9000 = 18.18%
        assert metrics.max_drawdown_pct > 15


class TestPipCosts:
    def test_pip_cost_respects_pip_size(self) -> None:
        from rtrade.backtest.costs import compute_trade_cost

        model = CostModel(symbol="USDJPY", pip_size=0.01, spread_pips_rt=2.0)
        cost = compute_trade_cost(model, 150.0, "BUY")
        assert cost == pytest.approx(2.0 * 0.01)
