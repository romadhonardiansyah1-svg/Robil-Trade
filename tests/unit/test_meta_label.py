"""Unit tests for meta-labeling XGBoost (P3-T6)."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from rtrade.ml.meta_label import MetaLabeler


def _make_trade_data(n: int = 200) -> list[dict]:
    """Generate synthetic trade data for meta-labeling."""
    np.random.seed(42)
    trades = []

    for i in range(n):
        # Higher confluence → more likely TP.
        confluence = np.random.randint(40, 95)
        # P(TP) loosely correlated with confluence.
        tp_prob = min(0.9, confluence / 100 + 0.1)
        outcome_r = (
            np.random.uniform(1.0, 3.0)
            if np.random.random() < tp_prob
            else np.random.uniform(-1.5, -0.5)
        )

        trades.append(
            {
                "outcome_r": outcome_r,
                "confluence_trend": np.random.randint(5, 25),
                "confluence_momentum": np.random.randint(0, 20),
                "confluence_structure": np.random.randint(0, 20),
                "confluence_volume": np.random.randint(0, 15),
                "confluence_macro": np.random.randint(0, 20),
                "confluence_score": confluence,
                "rsi": np.random.uniform(25, 75),
                "adx": np.random.uniform(10, 40),
                "atr_percentile": np.random.uniform(10, 90),
                "ema_alignment": np.random.choice([True, False]),
                "regime": np.random.choice(["TREND", "RANGE"]),
                "rr_ratio": np.random.uniform(1.5, 3.0),
                "sl_atr_mult": np.random.uniform(0.5, 2.5),
                "bar_ts": datetime(2026, 1, 1 + i % 28, i % 24, tzinfo=UTC),
            }
        )

    return trades


class TestMetaLabelerPrepareLabels:
    def test_label_creation(self) -> None:
        ml = MetaLabeler()
        trades = _make_trade_data(50)
        df = ml.prepare_labels(trades)

        assert "label" in df.columns
        assert set(df["label"].unique()).issubset({0, 1})
        assert len(df) == 50

    def test_positive_outcome_labeled_1(self) -> None:
        ml = MetaLabeler()
        trades = [
            {
                "outcome_r": 2.0,
                "confluence_score": 70,
                "bar_ts": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ]
        df = ml.prepare_labels(trades)
        assert df.iloc[0]["label"] == 1

    def test_negative_outcome_labeled_0(self) -> None:
        ml = MetaLabeler()
        trades = [
            {
                "outcome_r": -1.0,
                "confluence_score": 70,
                "bar_ts": datetime(2026, 1, 1, tzinfo=UTC),
            }
        ]
        df = ml.prepare_labels(trades)
        assert df.iloc[0]["label"] == 0


class TestMetaLabelerTraining:
    def test_train_succeeds(self) -> None:
        ml = MetaLabeler(n_estimators=10, max_depth=3)
        trades = _make_trade_data(200)
        df = ml.prepare_labels(trades)

        evaluation = ml.train(df)

        assert ml.is_trained
        assert 0 <= evaluation.accuracy <= 1
        assert 0 <= evaluation.auc_roc <= 1
        assert evaluation.n_samples == 200
        assert evaluation.n_folds >= 2

    def test_train_insufficient_data(self) -> None:
        ml = MetaLabeler()
        trades = _make_trade_data(20)
        df = ml.prepare_labels(trades)

        with pytest.raises(ValueError, match="insufficient"):
            ml.train(df)

    def test_feature_importances(self) -> None:
        ml = MetaLabeler(n_estimators=10, max_depth=3)
        trades = _make_trade_data(200)
        df = ml.prepare_labels(trades)
        ml.train(df)

        imps = ml.feature_importances
        assert len(imps) > 0
        assert all(v >= 0 for v in imps.values())


class TestMetaLabelerPrediction:
    def test_predict_returns_result(self) -> None:
        ml = MetaLabeler(n_estimators=10, max_depth=3)
        trades = _make_trade_data(200)
        df = ml.prepare_labels(trades)
        ml.train(df)

        features = {
            "confluence_trend": 20,
            "confluence_momentum": 15,
            "confluence_structure": 15,
            "confluence_volume": 10,
            "confluence_macro": 10,
            "confluence_score": 70,
            "rsi": 45,
            "adx": 30,
            "atr_percentile": 50,
            "ema_alignment": 1,
            "regime_trend": 1,
            "regime_range": 0,
            "rr_ratio": 2.0,
            "sl_atr_mult": 1.0,
        }

        result = ml.predict(features)
        assert 0 <= result.probability <= 1
        assert isinstance(result.should_trade, bool)

    def test_predict_without_training_raises(self) -> None:
        ml = MetaLabeler()
        with pytest.raises(RuntimeError, match="not trained"):
            ml.predict({"confluence_score": 70})
