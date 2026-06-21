"""Unit tests for the indicator engine (PLAN §12.2 — golden tests)."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from rtrade.indicators.engine import compute, snapshot


def _make_ohlcv_df(n: int = 300) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing."""
    np.random.seed(42)

    base_price = 2700.0
    dates = pd.date_range(start=datetime(2026, 1, 1, tzinfo=UTC), periods=n, freq="1h")

    close = np.cumsum(np.random.randn(n) * 2) + base_price
    high = close + np.abs(np.random.randn(n) * 3)
    low = close - np.abs(np.random.randn(n) * 3)
    open_ = close + np.random.randn(n) * 1

    # Ensure OHLC validity.
    for i in range(n):
        high[i] = max(high[i], open_[i], close[i])
        low[i] = min(low[i], open_[i], close[i])

    volume = np.abs(np.random.randn(n) * 1000) + 100

    df = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )
    return df


class TestIndicatorEngine:
    """Smoke tests for the indicator engine."""

    def test_compute_returns_all_columns(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)

        expected_cols = {
            "ema21",
            "ema50",
            "ema200",
            "rsi",
            "atr",
            "adx",
            "plus_di",
            "minus_di",
            "macd",
            "macd_signal",
            "macd_hist",
            "bb_upper",
            "bb_mid",
            "bb_lower",
            "vwap",
            "atr_percentile",
        }
        for col in expected_cols:
            assert col in result.columns, f"missing column: {col}"

    def test_ema_values_reasonable(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)

        # EMA200 should be NaN for early rows but defined for later.
        assert not np.isnan(result["ema200"].iloc[-1])
        assert not np.isnan(result["ema50"].iloc[-1])
        assert not np.isnan(result["ema21"].iloc[-1])

    def test_rsi_bounded(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)
        rsi = result["rsi"].dropna()
        assert rsi.min() >= 0
        assert rsi.max() <= 100

    def test_atr_positive(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)
        atr = result["atr"].dropna()
        assert (atr > 0).all()

    def test_atr_percentile_bounded(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)
        pct = result["atr_percentile"].dropna()
        assert pct.min() >= 0
        assert pct.max() <= 100

    def test_snapshot_extracts_last_bar(self) -> None:
        df = _make_ohlcv_df()
        result = compute(df)
        snap = snapshot(result)

        assert snap.rsi >= 0
        assert snap.atr > 0
        assert snap.bar_ts == pd.Timestamp(result.index[-1])

    def test_empty_df_rejected(self) -> None:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        with pytest.raises(AssertionError):
            compute(df)

    def test_missing_columns_rejected(self) -> None:
        df = pd.DataFrame({"close": [100, 101, 102]})
        with pytest.raises(AssertionError):
            compute(df)


class TestComputePurity:
    """F2: compute() must not mutate the caller's DataFrame."""

    def test_compute_does_not_mutate_input(self) -> None:
        df = _make_ohlcv_df(50)
        original_cols = list(df.columns)
        original_dtypes = df.dtypes.to_dict()
        original_index = df.index.copy()
        # Snapshot the raw values so we can detect in-place dtype coercion.
        original_values = df.copy(deep=True)

        result = compute(df)

        # The original frame must be untouched: same columns, no indicator
        # columns leaked back, identical dtypes, identical values.
        assert list(df.columns) == original_cols, "compute() added columns to input"
        assert df.dtypes.to_dict() == original_dtypes, "compute() coerced input dtypes"
        assert df.index.equals(original_index), "compute() altered the input index"
        pd.testing.assert_frame_equal(df, original_values)

        # And the returned frame is a distinct object that DID get indicators.
        assert result is not df
        assert "vwap" in result.columns


def _make_two_day_intraday_df() -> pd.DataFrame:
    """Two UTC calendar days of hourly bars with non-trivial volume.

    Constant per-bar values are avoided so the no-reset cumulative VWAP
    genuinely differs from the daily-anchored one.
    """
    # 6 bars on day 1, 6 bars on day 2.
    day1 = pd.date_range(start=datetime(2026, 3, 1, 18, tzinfo=UTC), periods=6, freq="1h")
    day2 = pd.date_range(start=datetime(2026, 3, 2, 0, tzinfo=UTC), periods=6, freq="1h")
    index = day1.append(day2)

    close = np.array([100.0, 110, 120, 130, 140, 150, 200, 210, 220, 230, 240, 250])
    high = close + 2.0
    low = close - 2.0
    open_ = close - 1.0
    volume = np.array([10.0, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


class TestVwapDailyAnchor:
    """F1: VWAP must reset accumulation at the first bar of each UTC day."""

    def test_vwap_resets_on_first_bar_of_new_utc_day(self) -> None:
        df = _make_two_day_intraday_df()
        result = compute(df)

        # First bar of day 2 (index 6): with a daily reset, the cumulative
        # VWAP equals that single bar's typical price.
        day2_open = result.index[6]
        assert day2_open.date() == datetime(2026, 3, 2, tzinfo=UTC).date()
        typical = (result["high"].iloc[6] + result["low"].iloc[6] + result["close"].iloc[6]) / 3
        assert result["vwap"].iloc[6] == pytest.approx(typical, rel=1e-9)

    def test_vwap_differs_from_non_reset_cumulative(self) -> None:
        df = _make_two_day_intraday_df()
        result = compute(df)

        # Whole-frame (no reset) cumulative VWAP at the last bar.
        typical = (df["high"] + df["low"] + df["close"]) / 3
        no_reset = (typical * df["volume"]).cumsum() / df["volume"].cumsum()

        # The daily-anchored value at the last bar must differ because day-2
        # accumulation excludes day-1 bars.
        assert result["vwap"].iloc[-1] != pytest.approx(no_reset.iloc[-1], rel=1e-9)

        # Sanity: day-2 anchored VWAP equals the day-2-only cumulative.
        day2 = df.iloc[6:]
        typ2 = (day2["high"] + day2["low"] + day2["close"]) / 3
        expected = (typ2 * day2["volume"]).cumsum() / day2["volume"].cumsum()
        assert result["vwap"].iloc[-1] == pytest.approx(expected.iloc[-1], rel=1e-9)


class TestStructure:
    """Tests for market structure analysis."""

    def test_swing_points_detected(self) -> None:
        from rtrade.indicators.structure import detect_swing_points

        df = _make_ohlcv_df(50)
        points = detect_swing_points(df)
        assert len(points) > 0

        # All points should have valid indices.
        for p in points:
            assert 2 <= p.index <= len(df) - 3

    def test_sr_clustering(self) -> None:
        from rtrade.indicators.structure import (
            SwingPoint,
            cluster_sr_levels,
        )

        ts = pd.Timestamp("2026-01-01")
        points = [
            SwingPoint(index=0, price=100.0, is_high=True, ts=ts),
            SwingPoint(index=5, price=100.5, is_high=True, ts=ts),
            SwingPoint(index=10, price=100.3, is_high=True, ts=ts),
            SwingPoint(index=15, price=200.0, is_high=False, ts=ts),
        ]
        levels = cluster_sr_levels(points, atr=5.0, min_touches=2)
        assert len(levels) >= 1
        # The cluster around 100.x should have strength ≥ 2.
        assert any(lvl.strength >= 2 for lvl in levels)

    def test_gap_detection(self) -> None:
        from rtrade.indicators.structure import detect_gaps

        df = _make_ohlcv_df(50)
        result = compute(df)
        atr = float(result["atr"].dropna().iloc[-1])
        gaps = detect_gaps(df, atr)
        # Gaps may or may not exist in random data, but function should not crash.
        assert isinstance(gaps, list)

    # ------------------------------------------------------------------
    # F6: S/R clustering must use single-linkage on consecutive gaps,
    # not a drifting running mean (deterministic, order-independent).
    # ------------------------------------------------------------------

    def test_sr_clustering_single_linkage_chain(self) -> None:
        """A price-chain with every consecutive gap <= tolerance is ONE cluster.

        With atr=4.0 and mult 0.25 the tolerance is 1.0. Points spaced 0.9
        apart chain into a single cluster under single-linkage. The old
        drifting-mean method split this same chain into several clusters
        because the lagging mean exceeded tolerance partway through.
        """
        from rtrade.indicators.structure import SwingPoint, cluster_sr_levels

        ts = pd.Timestamp("2026-01-01", tz="UTC")
        prices = [100.0, 100.9, 101.8, 102.7, 103.6]
        points = [SwingPoint(index=i, price=p, is_high=True, ts=ts) for i, p in enumerate(prices)]
        levels = cluster_sr_levels(points, atr=4.0, min_touches=1)

        assert len(levels) == 1
        assert levels[0].strength == 5
        assert levels[0].price == pytest.approx(101.8)

    def test_sr_clustering_order_independent(self) -> None:
        """Shuffling the input yields identical levels (order-independent)."""
        import random

        from rtrade.indicators.structure import SwingPoint, cluster_sr_levels

        ts = pd.Timestamp("2026-01-01", tz="UTC")
        prices = [100.0, 100.4, 100.7, 105.0, 105.3, 110.0, 110.2, 110.5]
        points = [
            SwingPoint(index=i, price=p, is_high=(i % 2 == 0), ts=ts) for i, p in enumerate(prices)
        ]
        baseline = cluster_sr_levels(points, atr=4.0, min_touches=2)

        shuffled = points[:]
        random.Random(123).shuffle(shuffled)
        result = cluster_sr_levels(shuffled, atr=4.0, min_touches=2)

        assert [(lvl.price, lvl.strength, lvl.is_resistance) for lvl in baseline] == [
            (lvl.price, lvl.strength, lvl.is_resistance) for lvl in result
        ]

    def test_sr_clustering_split_on_gap(self) -> None:
        """A consecutive gap > tolerance starts a new cluster."""
        from rtrade.indicators.structure import SwingPoint, cluster_sr_levels

        ts = pd.Timestamp("2026-01-01", tz="UTC")
        # tolerance = 0.25 * 4.0 = 1.0; the 105 group is > 1.0 from the 100 group.
        prices = [100.0, 100.5, 105.0, 105.4]
        points = [SwingPoint(index=i, price=p, is_high=True, ts=ts) for i, p in enumerate(prices)]
        levels = cluster_sr_levels(points, atr=4.0, min_touches=2)

        assert len(levels) == 2
        assert levels[0].price == pytest.approx(100.25)
        assert levels[1].price == pytest.approx(105.2)

    def test_sr_clustering_tie_break_is_resistance(self) -> None:
        """Equal-high/low count ties resolve deterministically to resistance."""
        from rtrade.indicators.structure import SwingPoint, cluster_sr_levels

        ts = pd.Timestamp("2026-01-01", tz="UTC")
        points = [
            SwingPoint(index=0, price=100.0, is_high=True, ts=ts),
            SwingPoint(index=1, price=100.2, is_high=False, ts=ts),
        ]
        levels = cluster_sr_levels(points, atr=4.0, min_touches=2)

        assert len(levels) == 1
        assert levels[0].is_resistance is True

    # ------------------------------------------------------------------
    # F7: equal-high/low (double tops/bottoms) must be detected without
    # spamming every bar of a flat top.
    # ------------------------------------------------------------------

    def _swing_df(self, highs: list[float], lows: list[float]) -> pd.DataFrame:
        n = len(highs)
        idx = pd.date_range(start=datetime(2026, 1, 1, tzinfo=UTC), periods=n, freq="1h")
        return pd.DataFrame(
            {
                "open": highs,
                "high": highs,
                "low": lows,
                "close": highs,
                "volume": [1.0] * n,
            },
            index=idx,
        )

    def test_swing_detects_double_top_in_window(self) -> None:
        """Two equal highs inside one window are NOT dropped (liquidity pool)."""
        from rtrade.indicators.structure import detect_swing_points

        # Two equal highs at 15 within a 5-bar window. The old `== 1`
        # uniqueness check dropped BOTH; at least one must survive.
        highs = [10.0, 11.0, 15.0, 12.0, 15.0, 11.0, 10.0]
        lows = [5.0] * len(highs)
        df = self._swing_df(highs, lows)

        points = detect_swing_points(df)
        swing_highs_at_15 = [p for p in points if p.is_high and p.price == 15.0]
        assert len(swing_highs_at_15) >= 1

    def test_swing_flat_top_yields_single_high(self) -> None:
        """A flat top of 3+ equal bars yields EXACTLY one swing high (no spam)."""
        from rtrade.indicators.structure import detect_swing_points

        highs = [10.0, 11.0, 15.0, 15.0, 15.0, 11.0, 10.0]
        lows = [5.0] * len(highs)
        df = self._swing_df(highs, lows)

        points = detect_swing_points(df)
        swing_highs_at_15 = [p for p in points if p.is_high and p.price == 15.0]
        assert len(swing_highs_at_15) == 1
        # The single point must be the LEFTMOST bar of the flat top.
        assert swing_highs_at_15[0].index == 2

    def test_swing_detects_double_bottom_in_window(self) -> None:
        """Symmetric: two equal lows inside one window are detected."""
        from rtrade.indicators.structure import detect_swing_points

        lows = [20.0, 19.0, 15.0, 18.0, 15.0, 19.0, 20.0]
        highs = [25.0] * len(lows)
        df = self._swing_df(highs, lows)

        points = detect_swing_points(df)
        swing_lows_at_15 = [p for p in points if not p.is_high and p.price == 15.0]
        assert len(swing_lows_at_15) >= 1
