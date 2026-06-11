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
