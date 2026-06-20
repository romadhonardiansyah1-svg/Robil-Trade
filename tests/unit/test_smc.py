"""Unit + property tests for SMC/ICT detectors (indicators/smc.py)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
import pandas as pd

OHLC = tuple[float, float, float, float]


def _df(rows: list[OHLC]) -> pd.DataFrame:
    """Build an OHLC DataFrame from explicit (open, high, low, close) bars."""
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
        },
        index=idx,
    )


# 5 bars: a bullish 3-bar gap at i=2 (low[2]=12.5 > high[0]=11),
# and a bearish 3-bar gap at i=4 (high[4]=9.5 < low[2]=12.5).
_FVG_ROWS: list[OHLC] = [
    (10.0, 11.0, 9.0, 10.0),
    (11.0, 12.0, 10.0, 11.0),
    (13.0, 15.0, 12.5, 14.0),
    (14.0, 14.5, 11.5, 12.0),
    (9.0, 9.5, 8.0, 8.5),
]


class TestFairValueGaps:
    def test_detects_bullish_and_bearish_gaps_with_exact_bounds(self) -> None:
        from rtrade.indicators.smc import FairValueGap, fair_value_gaps

        gaps = fair_value_gaps(_df(_FVG_ROWS))
        assert gaps == [
            FairValueGap(start_idx=0, end_idx=2, top=12.5, bottom=11.0, direction="bullish"),
            FairValueGap(start_idx=2, end_idx=4, top=12.5, bottom=9.5, direction="bearish"),
        ]

    def test_no_gap_when_bars_overlap(self) -> None:
        from rtrade.indicators.smc import fair_value_gaps

        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 11.0, 9.0, 10.0),
        ]
        assert fair_value_gaps(_df(rows)) == []

    def test_too_short_returns_empty(self) -> None:
        from rtrade.indicators.smc import fair_value_gaps

        assert fair_value_gaps(_df(_FVG_ROWS[:2])) == []


@settings(max_examples=100, deadline=None)
@given(
    rows=st.lists(
        st.tuples(
            st.floats(min_value=1.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False),
        ),
        min_size=3,
        max_size=40,
    )
)
def test_fvg_top_always_strictly_above_bottom(
    rows: list[tuple[float, float, float, float]],
) -> None:
    from rtrade.indicators.smc import fair_value_gaps

    built: list[OHLC] = []
    for base, up, down, coff in rows:
        high = base + up
        low = base - down
        close = min(max(base + coff, low), high)
        built.append((base, high, low, close))
    for fvg in fair_value_gaps(_df(built)):
        assert fvg.top > fvg.bottom


# 12 bars: swing high @3 (110) broken at close[6]=111 -> bullish BOS @6;
# swing low @8 (107) broken at close[11]=105 -> bearish CHoCH @11.
_MS_ROWS: list[OHLC] = [
    (100.0, 102.0, 99.0, 101.0),
    (101.0, 103.0, 100.0, 102.0),
    (102.0, 104.0, 101.0, 103.0),
    (103.0, 110.0, 102.0, 104.0),
    (104.0, 106.0, 103.0, 105.0),
    (108.0, 108.5, 104.0, 106.0),
    (106.0, 112.0, 109.0, 111.0),
    (111.0, 113.0, 109.0, 110.0),
    (110.0, 111.0, 107.0, 108.0),
    (108.0, 110.0, 108.0, 109.0),
    (109.0, 111.0, 108.0, 110.0),
    (108.0, 109.0, 104.0, 105.0),
]


class TestMarketStructure:
    def test_bos_then_choch_with_exact_indices(self) -> None:
        from rtrade.indicators.smc import StructureEvent, market_structure

        events = market_structure(_df(_MS_ROWS), swing_lookback=2)
        assert events == [
            StructureEvent(idx=6, kind="BOS", direction="bullish"),
            StructureEvent(idx=11, kind="CHoCH", direction="bearish"),
        ]

    def test_no_events_without_breaks(self) -> None:
        from rtrade.indicators.smc import market_structure

        flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 8
        assert market_structure(_df(flat), swing_lookback=2) == []


class TestOrderBlocks:
    def test_last_opposing_candle_before_each_break(self) -> None:
        from rtrade.indicators.smc import OrderBlock, order_blocks

        blocks = order_blocks(_df(_MS_ROWS), swing_lookback=2)
        assert blocks == [
            OrderBlock(idx=5, top=108.5, bottom=104.0, direction="bullish"),
            OrderBlock(idx=10, top=111.0, bottom=108.0, direction="bearish"),
        ]

    def test_no_blocks_without_structure(self) -> None:
        from rtrade.indicators.smc import order_blocks

        flat: list[OHLC] = [(100.0, 101.0, 99.0, 100.0)] * 8
        assert order_blocks(_df(flat), swing_lookback=2) == []


class TestLiquiditySweeps:
    def test_high_side_sweep_exact(self) -> None:
        from rtrade.indicators.smc import LiquiditySweep, liquidity_sweeps

        # swing high @2 = 15; bar @5 wicks to 16 but closes 12.5 (back inside).
        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 12.0, 9.0, 11.0),
            (11.0, 15.0, 10.0, 12.0),
            (11.0, 13.0, 10.0, 12.0),
            (12.0, 13.0, 11.0, 12.0),
            (12.0, 16.0, 11.0, 12.5),
        ]
        sweeps = liquidity_sweeps(_df(rows), swing_lookback=2)
        assert sweeps == [LiquiditySweep(idx=5, level=15.0, side="high")]

    def test_low_side_sweep_exact(self) -> None:
        from rtrade.indicators.smc import LiquiditySweep, liquidity_sweeps

        # swing low @2 = 15; bar @5 wicks to 14 but closes 17.5 (back inside).
        rows: list[OHLC] = [
            (20.0, 21.0, 19.0, 20.0),
            (20.0, 21.0, 18.0, 19.0),
            (19.0, 20.0, 15.0, 18.0),
            (18.0, 19.0, 16.0, 17.0),
            (17.0, 18.0, 16.0, 17.0),
            (17.0, 18.0, 14.0, 17.5),
        ]
        sweeps = liquidity_sweeps(_df(rows), swing_lookback=2)
        assert sweeps == [LiquiditySweep(idx=5, level=15.0, side="low")]

    def test_no_sweep_when_close_breaks_through(self) -> None:
        from rtrade.indicators.smc import liquidity_sweeps

        # swing high @2 = 15; bar @5 closes ABOVE 15 -> a break, not a sweep.
        rows: list[OHLC] = [
            (10.0, 11.0, 9.0, 10.0),
            (10.0, 12.0, 9.0, 11.0),
            (11.0, 15.0, 10.0, 12.0),
            (11.0, 13.0, 10.0, 12.0),
            (12.0, 13.0, 11.0, 12.0),
            (12.0, 16.0, 11.0, 15.5),
        ]
        assert liquidity_sweeps(_df(rows), swing_lookback=2) == []
