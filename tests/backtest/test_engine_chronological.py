"""A10: the equity curve must be accumulated in chronological (exit-time) order.

`run_backtest` previously booked each trade's PnL into the equity curve in
*signal* order. When trades overlap in time that distorts the curve shape and
therefore `max_drawdown_pct` (and any curve-derived metric), because the booked
order no longer matches when capital was actually won/lost.

These tests pin the correct behaviour:
  * overlapping trades -> the curve reflects EXIT-TIME order, exposing a
    drawdown that signal order hides;
  * non-overlapping trades -> the curve is byte-for-byte the sequential
    signal-order accumulation (no regression).
"""

from __future__ import annotations

import pandas as pd

from rtrade.backtest.engine import run_backtest
from rtrade.backtest.metrics import compute_metrics


def _df_from_bars(bars: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a tz-aware UTC OHLCV frame from explicit (open, high, low, close) bars."""
    opens = [b[0] for b in bars]
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]
    closes = [b[3] for b in bars]
    index = pd.date_range("2026-01-01", periods=len(bars), freq="1h", tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1000},
        index=index,
    )


class TestChronologicalEquityCurve:
    def test_overlapping_trades_curve_is_exit_time_ordered(self) -> None:
        """A opens first but exits LAST (+20R); B opens later but exits EARLIER (-1R).

        Signal-order accumulation books the +20R winner first (peak 12000) so the
        later -1R loss looks like a 1.0% blip off an inflated peak. Chronologically
        the loss is realized FIRST (a 1.2% drawdown off the starting capital) and the
        winner only closes afterwards. The curve must reflect that real chronology.
        """
        bars = [
            (100.0, 101.0, 99.5, 100.0),  # 0  A signal
            (100.0, 100.5, 99.8, 100.0),  # 1  A fills @100
            (100.0, 101.0, 99.5, 100.0),  # 2  B signal
            (100.0, 100.5, 99.5, 100.0),  # 3  B fills @100
            (100.0, 100.5, 99.5, 100.0),  # 4  both open
            (100.0, 100.2, 98.5, 99.0),  # 5  B stops out @99 (A's SL=90 survives)
            (100.0, 101.0, 99.0, 100.0),  # 6  A open
            (100.0, 150.0, 99.0, 140.0),  # 7  A rallying
            (140.0, 305.0, 139.0, 300.0),  # 8  A take-profit @300 (LAST exit)
            (300.0, 301.0, 299.0, 300.0),  # 9
            (300.0, 301.0, 299.0, 300.0),  # 10
        ]
        df = _df_from_bars(bars)
        signals = [
            {  # Trade A: wide stop, huge TP, exits last.
                "bar_index": 0,
                "direction": "BUY",
                "entry_limit": 100.0,
                "stop_loss": 90.0,
                "take_profit": 300.0,
                "valid_bars": 6,
            },
            {  # Trade B: tight stop, opens later, exits earlier (loss).
                "bar_index": 2,
                "direction": "BUY",
                "entry_limit": 100.0,
                "stop_loss": 99.0,
                "take_profit": 130.0,
                "valid_bars": 6,
            },
        ]

        result = run_backtest(df, signals, initial_equity=10_000.0, risk_pct=1.0)

        trade_a, trade_b = result.trades
        # Sanity: confirm the overlap and the exit ordering we engineered.
        assert trade_a.fill_bar == 1
        assert trade_a.exit_bar == 8
        assert trade_a.exit_reason == "TP"
        assert trade_b.fill_bar == 3
        assert trade_b.exit_bar == 5
        assert trade_b.exit_reason == "SL"
        assert trade_a.r_multiple == 20.0
        assert trade_b.r_multiple == -1.0

        # The trade log itself stays in signal order (A then B).
        assert trade_a.bar_index == 0
        assert trade_b.bar_index == 2

        # pnl: A = 20R * (10000*1%) = +2000 ; B = -1R * (12000*1%) = -120.
        # Chronological (exit-time) accumulation books B (exit_bar 5) before A
        # (exit_bar 8): 10000 -> 9880 -> 11880.
        assert result.equity_curve == [10_000.0, 9_880.0, 11_880.0]
        assert result.final_equity == 11_880.0

        # The old signal-order curve would have been 10000 -> 12000 -> 11880.
        assert result.equity_curve != [10_000.0, 12_000.0, 11_880.0]

        r_multiples = [t.r_multiple for t in result.trades if t.r_multiple is not None]
        chrono_dd = compute_metrics(r_multiples, result.equity_curve).max_drawdown_pct
        signal_dd = compute_metrics(r_multiples, [10_000.0, 12_000.0, 11_880.0]).max_drawdown_pct
        # Chronology exposes the deeper 1.2% drawdown; signal order hides it at 1.0%.
        assert chrono_dd > signal_dd
        assert round(chrono_dd, 4) == 1.2
        assert round(signal_dd, 4) == 1.0

    def test_non_overlapping_trades_match_sequential_accumulation(self) -> None:
        """When trades don't overlap, exit-time order == signal order: no change.

        A fully exits (bar 2) before B fills (bar 5), so the curve must be the plain
        sequential signal-order accumulation it always was.
        """
        bars = [
            (100.0, 101.0, 99.5, 100.0),  # 0  A signal
            (100.0, 100.5, 99.8, 100.0),  # 1  A fills @100
            (100.0, 111.0, 99.0, 110.0),  # 2  A take-profit @110 (+1R)
            (110.0, 111.0, 109.0, 110.0),  # 3
            (110.0, 111.0, 109.0, 110.0),  # 4  B signal
            (110.0, 111.0, 109.5, 110.0),  # 5  B fills @110
            (110.0, 111.0, 109.0, 110.0),  # 6  B open
            (110.0, 121.0, 109.0, 120.0),  # 7  B take-profit @120 (+1R)
            (120.0, 121.0, 119.0, 120.0),  # 8
            (120.0, 121.0, 119.0, 120.0),  # 9
        ]
        df = _df_from_bars(bars)
        signals = [
            {
                "bar_index": 0,
                "direction": "BUY",
                "entry_limit": 100.0,
                "stop_loss": 90.0,
                "take_profit": 110.0,
                "valid_bars": 6,
            },
            {
                "bar_index": 4,
                "direction": "BUY",
                "entry_limit": 110.0,
                "stop_loss": 100.0,
                "take_profit": 120.0,
                "valid_bars": 6,
            },
        ]

        result = run_backtest(df, signals, initial_equity=10_000.0, risk_pct=1.0)

        trade_a, trade_b = result.trades
        assert trade_a.exit_bar == 2
        assert trade_b.fill_bar == 5
        assert trade_a.exit_bar < trade_b.fill_bar  # no overlap

        # pnl: A = +1R * (10000*1%) = +100 ; B = +1R * (10100*1%) = +101.
        # Sequential == chronological here.
        assert result.equity_curve == [10_000.0, 10_100.0, 10_201.0]
        assert result.final_equity == 10_201.0
