"""Unit tests for meta-labeling XGBoost (P3-T6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sklearn.model_selection import TimeSeriesSplit

from rtrade.ml.meta_label import MetaLabeler


def _make_oos_trades(n: int = 60) -> list[dict]:
    """Deterministic labeled set whose OOS mean differs from the full mean.

    Even indices are winners whose R grows over time; odd indices are
    constant losers. Because TimeSeriesSplit's out-of-fold rows are the
    *later* rows, their mean outcome_r is strictly larger than the mean
    over all rows -> in-sample vs OOS expectancy must differ.
    """
    trades = []
    for i in range(n):
        if i % 2 == 0:
            outcome_r = 1.0 + i * 0.05  # winners grow over time
        else:
            outcome_r = -1.0  # constant losers
        trades.append(
            {
                "outcome_r": outcome_r,
                "confluence_score": 80 if outcome_r > 0 else 40,
                "confluence_trend": 20 if outcome_r > 0 else 5,
                "rsi": 50,
                "bar_ts": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i),
            }
        )
    return trades


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

    def test_outcome_r_carried_into_dataframe(self) -> None:
        # G1a: prepare_labels must carry outcome_r through so the gate can use it.
        ml = MetaLabeler()
        trades = [
            {"outcome_r": 2.5, "confluence_score": 70, "bar_ts": datetime(2026, 1, 1, tzinfo=UTC)},
            {"outcome_r": -1.2, "confluence_score": 40, "bar_ts": datetime(2026, 1, 2, tzinfo=UTC)},
        ]
        df = ml.prepare_labels(trades)
        assert "outcome_r" in df.columns
        # Sorted by bar_ts -> same input order here.
        assert list(df["outcome_r"]) == [2.5, -1.2]

    def test_outcome_r_is_not_a_training_feature(self) -> None:
        # G1a: outcome_r is the target magnitude; including it would leak the label.
        assert "outcome_r" not in MetaLabeler.FEATURE_COLUMNS


class TestMetaLabelerOOSExpectancy:
    def test_expectancy_unfiltered_is_oos_mean_not_in_sample(self) -> None:
        # G1b: the gate's unfiltered expectancy must be the mean of out-of-fold
        # outcome_r, not 0.0 (the old dropped-column bug) and not the in-sample
        # full-dataset mean.
        ml = MetaLabeler(n_estimators=10, max_depth=3)
        trades = _make_oos_trades(60)
        df = ml.prepare_labels(trades)

        evaluation = ml.train(df)

        # Reconstruct the OOS (out-of-fold) test indices the same way train() does.
        X = df[ml.FEATURE_COLUMNS].values
        n_folds = min(5, len(X) // 30)
        tscv = TimeSeriesSplit(n_splits=max(2, n_folds))
        oos_idx: list[int] = []
        for _train_idx, test_idx in tscv.split(X):
            oos_idx.extend(test_idx.tolist())

        outcome_r = df["outcome_r"].to_numpy()
        oos_mean = float(outcome_r[oos_idx].mean())
        full_mean = float(outcome_r.mean())

        assert evaluation.expectancy_unfiltered == round(oos_mean, 4)
        assert evaluation.expectancy_unfiltered != 0.0
        # Proves it is OOS, not in-sample.
        assert round(oos_mean, 4) != round(full_mean, 4)


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
