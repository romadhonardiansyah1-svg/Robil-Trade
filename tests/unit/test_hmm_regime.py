"""Unit tests for HMM regime detector (P3-T5)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from rtrade.core.constants import Regime
from rtrade.regime.hmm import HMMRegimeDetector


def _make_synthetic_df(
    n_bars: int = 1000,
    trend_frac: float = 0.4,
    range_frac: float = 0.4,
    crisis_frac: float = 0.2,
) -> tuple[pd.DataFrame, list[Regime]]:
    """Create synthetic OHLCV data with known regimes.

    Returns (df, regime_labels) where regime_labels[i] is the true
    regime for bar i.
    """
    np.random.seed(42)

    n_trend = int(n_bars * trend_frac)
    n_range = int(n_bars * range_frac)
    n_crisis = n_bars - n_trend - n_range

    # TREND: strong directional moves.
    trend_returns = np.random.normal(0.002, 0.01, n_trend)
    # RANGE: small oscillations.
    range_returns = np.random.normal(0.0, 0.003, n_range)
    # CRISIS: large volatile moves.
    crisis_returns = np.random.normal(-0.001, 0.04, n_crisis)

    all_returns = np.concatenate([trend_returns, range_returns, crisis_returns])
    labels = [Regime.TREND] * n_trend + [Regime.RANGE] * n_range + [Regime.CRISIS] * n_crisis

    # Build prices from returns.
    close = np.zeros(n_bars)
    close[0] = 100.0
    for i in range(1, n_bars):
        close[i] = close[i - 1] * (1 + all_returns[i])

    high = close * (1 + np.abs(np.random.normal(0, 0.005, n_bars)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n_bars)))
    open_ = close + np.random.normal(0, 0.001, n_bars) * close
    volume = np.random.uniform(1000, 10000, n_bars)

    # ATR approximation.
    atr = np.abs(high - low)

    df = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "atr": atr,
        }
    )

    return df, labels


class TestHMMTraining:
    def test_train_succeeds(self) -> None:
        """HMM should train on synthetic data without error."""
        df, _ = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector(train_window_bars=500, n_iter=20)
        ll = detector.train(df)

        assert detector.is_trained
        assert isinstance(ll, float)

    def test_train_insufficient_data(self) -> None:
        df, _ = _make_synthetic_df(n_bars=50)
        detector = HMMRegimeDetector(train_window_bars=50, n_iter=10)
        with pytest.raises(ValueError, match="insufficient"):
            detector.train(df)

    def test_state_mapping_has_all_regimes(self) -> None:
        """State map should contain TREND, RANGE, and CRISIS."""
        df, _ = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector(train_window_bars=500, n_iter=20)
        detector.train(df)

        mapped_regimes = set(detector._state_map.values())
        assert Regime.TREND in mapped_regimes
        assert Regime.RANGE in mapped_regimes
        assert Regime.CRISIS in mapped_regimes


class TestHMMClassification:
    def test_classify_returns_valid_regime(self) -> None:
        df, _ = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector(train_window_bars=500, n_iter=20)
        detector.train(df)

        state = detector.classify("XAUUSD", df)

        assert state.regime in (
            Regime.TREND,
            Regime.RANGE,
            Regime.CRISIS,
        )
        assert 0 <= state.probability <= 1
        assert state.state_id in (0, 1, 2)

    def test_classify_without_training_raises(self) -> None:
        df, _ = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector()

        with pytest.raises(RuntimeError, match="not trained"):
            detector.classify("XAUUSD", df)

    def test_hysteresis_preserves_since(self) -> None:
        """Same regime → 'since' should not change."""
        df, _ = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector(train_window_bars=500, n_iter=20)
        detector.train(df)

        now = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
        s1 = detector.classify("XAUUSD", df, now=now)

        # Classify again with same data.
        later = datetime(2026, 7, 1, 7, 0, tzinfo=UTC)
        s2 = detector.classify("XAUUSD", df, now=later)

        if s1.regime == s2.regime:
            assert s2.since == s1.since  # since preserved


class TestHMMComparison:
    def test_comparison_returns_metrics(self) -> None:
        df, labels = _make_synthetic_df(n_bars=500)
        detector = HMMRegimeDetector(train_window_bars=500, n_iter=20)
        detector.train(df)

        metrics = detector.compare_with_rule_based(df, labels)

        assert "total_samples" in metrics
        assert "agreement_rate_pct" in metrics
        assert "per_regime" in metrics
        assert metrics["total_samples"] > 0
        assert 0 <= metrics["agreement_rate_pct"] <= 100
