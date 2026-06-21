"""Meta-labeling with XGBoost (PLAN P3-T6).

STATUS: EXPERIMENTAL / INERT — intentionally dormant.

This module is NOT wired into the production signal path. Nothing in the
live scan pipeline imports or calls it, and ``predict()`` MUST NOT be called
from ``scan.py``. It is kept deliberately dormant (this is by design, not a
bug or dead code to be removed).

Per ADR-A08 and PLAN P3-6, meta-labeling stays gated behind a backtest
OOS-expectancy proof: it may only be promoted into the scan path once it is
demonstrated to raise expectancy out-of-sample via the backtest gate. If it
does not beat OOS expectancy it stays disabled (a negative result is a valid
result). Do not delete this module and do not import it into scan.py until
that gate has been passed.

Triple-barrier labeling from backtest results, then XGBoost binary
classifier to predict P(TP hit before SL).

Features: confluence breakdown components, regime, RSI, ATR percentile,
          ADX, EMA alignment score.

Validation: Purged + embargoed CV (prevent look-ahead in time-series).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit
import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MetaLabelResult:
    """Result of meta-label prediction for a candidate."""

    probability: float  # P(TP before SL)
    should_trade: bool  # probability >= threshold
    feature_importances: dict[str, float] | None = None


@dataclass(frozen=True, slots=True)
class MetaLabelEvaluation:
    """Evaluation metrics from purged walk-forward CV."""

    accuracy: float
    precision: float
    recall: float
    f1: float
    auc_roc: float
    n_folds: int
    n_samples: int
    expectancy_filtered: float  # OOS expectancy using only should_trade=True
    expectancy_unfiltered: float  # OOS expectancy without filter
    improvement_pct: float  # % improvement in OOS expectancy


class MetaLabeler:
    """XGBoost-based meta-labeling filter for signal candidates.

    Trained on backtest outcomes (triple-barrier labels) to predict
    whether a signal will hit TP before SL.
    """

    FEATURE_COLUMNS = [
        "confluence_trend",
        "confluence_momentum",
        "confluence_structure",
        "confluence_volume",
        "confluence_macro",
        "confluence_score",
        "rsi",
        "adx",
        "atr_percentile",
        "ema_alignment",  # 1=all aligned, 0=not
        "regime_trend",  # 1 if TREND
        "regime_range",  # 1 if RANGE
        "rr_ratio",
        "sl_atr_mult",  # SL distance in ATR multiples
    ]

    def __init__(
        self,
        threshold: float = 0.5,
        n_estimators: int = 200,
        max_depth: int = 4,
        learning_rate: float = 0.05,
        min_child_weight: int = 10,
        subsample: float = 0.8,
        colsample_bytree: float = 0.8,
        embargo_bars: int = 5,
    ) -> None:
        self._threshold = threshold
        self._embargo = embargo_bars
        self._model: Any = None
        self._model_params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "learning_rate": learning_rate,
            "min_child_weight": min_child_weight,
            "subsample": subsample,
            "colsample_bytree": colsample_bytree,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
        }
        self._feature_importances: dict[str, float] = {}

    def prepare_labels(
        self,
        trades: list[dict[str, Any]],
    ) -> pd.DataFrame:
        """Convert backtest trade outcomes to triple-barrier labels.

        Args:
            trades: List of trade dicts with fields:
                - outcome_r: R-multiple result (positive=TP, negative=SL)
                - confluence_trend, momentum, structure, volume, macro
                - confluence_score, rsi, adx, atr_percentile
                - ema_alignment (bool), regime (str)
                - rr_ratio, sl_atr_mult
                - bar_ts (datetime)

        Returns:
            DataFrame with features + 'label' (1=TP hit, 0=SL hit) + 'outcome_r'
            (raw R-multiple, carried for the expectancy gate; NOT a feature).
        """
        rows = []
        for t in trades:
            outcome_r = t.get("outcome_r", 0)
            label = 1 if outcome_r > 0 else 0

            row: dict[str, Any] = {"label": label}

            # Carry the raw R-multiple through so the promotion gate can compute
            # expectancy. NOTE: outcome_r is the target's magnitude, NOT a
            # training feature — it is intentionally kept out of FEATURE_COLUMNS
            # (including it would leak the label).
            row["outcome_r"] = outcome_r

            # Confluence breakdown.
            row["confluence_trend"] = t.get("confluence_trend", 0)
            row["confluence_momentum"] = t.get("confluence_momentum", 0)
            row["confluence_structure"] = t.get("confluence_structure", 0)
            row["confluence_volume"] = t.get("confluence_volume", 0)
            row["confluence_macro"] = t.get("confluence_macro", 0)
            row["confluence_score"] = t.get("confluence_score", 0)

            # Indicators at signal time.
            row["rsi"] = t.get("rsi", 50)
            row["adx"] = t.get("adx", 25)
            row["atr_percentile"] = t.get("atr_percentile", 50)
            row["ema_alignment"] = 1 if t.get("ema_alignment", False) else 0

            # Regime one-hot.
            regime = t.get("regime", "TREND")
            row["regime_trend"] = 1 if regime == "TREND" else 0
            row["regime_range"] = 1 if regime == "RANGE" else 0

            # Level metrics.
            row["rr_ratio"] = t.get("rr_ratio", 2.0)
            row["sl_atr_mult"] = t.get("sl_atr_mult", 1.0)

            # Timestamp for ordering.
            row["bar_ts"] = t.get("bar_ts", datetime.min)

            rows.append(row)

        df = pd.DataFrame(rows)
        if "bar_ts" in df.columns:
            df = df.sort_values("bar_ts").reset_index(drop=True)

        return df

    def train(self, df: pd.DataFrame) -> MetaLabelEvaluation:
        """Train XGBoost with purged + embargoed walk-forward CV.

        Args:
            df: Output of prepare_labels() with features + label.

        Returns:
            MetaLabelEvaluation with accuracy metrics. Expectancy metrics
            (filtered/unfiltered/improvement) are computed OUT-OF-SAMPLE from
            the out-of-fold CV predictions, not the in-sample refit.
        """
        from sklearn.metrics import (
            accuracy_score,
            f1_score,
            precision_score,
            recall_score,
            roc_auc_score,
        )
        import xgboost as xgb

        X = df[self.FEATURE_COLUMNS].values
        y = df["label"].values
        outcome_r_all = df["outcome_r"].to_numpy() if "outcome_r" in df.columns else None

        if len(X) < 50:
            raise ValueError(f"insufficient training data: {len(X)} samples (need ≥50)")

        # Purged + embargoed time-series CV.
        n_folds = min(5, len(X) // 30)
        tscv = TimeSeriesSplit(n_splits=max(2, n_folds))

        fold_metrics: list[dict[str, float]] = []
        # Out-of-fold (OOS) collectors for the expectancy gate (ADR-A08).
        oos_proba: list[float] = []
        oos_outcome_r: list[float] = []

        for _fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
            # Embargo: remove `embargo_bars` from end of training.
            if len(train_idx) > self._embargo:
                train_idx = train_idx[: -self._embargo]

            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            model = xgb.XGBClassifier(**self._model_params)
            model.fit(
                X_train,
                y_train,
                eval_set=[(X_test, y_test)],
                verbose=False,
            )

            y_pred = model.predict(X_test)
            y_proba = model.predict_proba(X_test)[:, 1]

            # Collect OOS predictions + aligned outcomes for the expectancy gate.
            if outcome_r_all is not None:
                oos_proba.extend(float(p) for p in y_proba)
                oos_outcome_r.extend(float(r) for r in outcome_r_all[test_idx])

            fold_metrics.append(
                {
                    "accuracy": accuracy_score(y_test, y_pred),
                    "precision": precision_score(y_test, y_pred, zero_division=0),
                    "recall": recall_score(y_test, y_pred, zero_division=0),
                    "f1": f1_score(y_test, y_pred, zero_division=0),
                    "auc_roc": roc_auc_score(y_test, y_proba) if len(set(y_test)) > 1 else 0.5,
                }
            )

        # Train final model on all data.
        final_model = xgb.XGBClassifier(**self._model_params)
        final_model.fit(X, y, verbose=False)
        self._model = final_model

        # Feature importances.
        importances = final_model.feature_importances_
        self._feature_importances = {
            col: round(float(imp), 4)
            for col, imp in zip(self.FEATURE_COLUMNS, importances, strict=False)
        }

        # Average CV metrics.
        avg = {k: np.mean([m[k] for m in fold_metrics]) for k in fold_metrics[0]}

        # Compute the PROMOTION GATE expectancy OUT-OF-SAMPLE (ADR-A08).
        # Both expectancies are derived from out-of-fold predictions collected
        # during the CV loop above — never from the in-sample refit — so the
        # filtered expectancy cannot look ahead. The refit-on-all model
        # (self._model) is still kept for deployment/inference only.
        oos_r = np.asarray(oos_outcome_r, dtype=float)
        oos_p = np.asarray(oos_proba, dtype=float)

        if oos_r.size > 0:
            unfiltered_exp = float(oos_r.mean())
            mask = oos_p >= self._threshold
            filtered_exp = float(oos_r[mask].mean()) if mask.any() else unfiltered_exp
        else:
            unfiltered_exp = 0.0
            filtered_exp = 0.0

        improvement = 0.0
        if abs(unfiltered_exp) > 0.001:
            improvement = (filtered_exp - unfiltered_exp) / abs(unfiltered_exp) * 100

        evaluation = MetaLabelEvaluation(
            accuracy=round(float(avg["accuracy"]), 4),
            precision=round(float(avg["precision"]), 4),
            recall=round(float(avg["recall"]), 4),
            f1=round(float(avg["f1"]), 4),
            auc_roc=round(float(avg["auc_roc"]), 4),
            n_folds=len(fold_metrics),
            n_samples=len(X),
            expectancy_filtered=round(filtered_exp, 4),
            expectancy_unfiltered=round(unfiltered_exp, 4),
            improvement_pct=round(improvement, 1),
        )

        logger.info(
            "meta-labeler trained",
            accuracy=evaluation.accuracy,
            auc_roc=evaluation.auc_roc,
            improvement_pct=evaluation.improvement_pct,
            n_samples=evaluation.n_samples,
        )

        return evaluation

    def predict(self, features: dict[str, float]) -> MetaLabelResult:
        """Predict whether a signal candidate should be traded.

        Args:
            features: Dict with keys matching FEATURE_COLUMNS.

        Returns:
            MetaLabelResult with probability and trade decision.
        """
        if self._model is None:
            raise RuntimeError("meta-labeler not trained. Call train() first.")

        X = np.array([[features.get(col, 0) for col in self.FEATURE_COLUMNS]])
        proba = float(self._model.predict_proba(X)[0, 1])

        return MetaLabelResult(
            probability=round(proba, 4),
            should_trade=proba >= self._threshold,
            feature_importances=self._feature_importances,
        )

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def feature_importances(self) -> dict[str, float]:
        return self._feature_importances.copy()

    def save(self, path: Path) -> None:
        """Save model to disk with integrity sidecar."""
        if self._model is None:
            raise RuntimeError("no model to save")
        from rtrade.ml.model_io import save_model

        save_model(self._model, path)
        logger.info("meta-labeler saved", path=str(path))

    def load(self, path: Path) -> None:
        """Load model from disk with integrity verification."""
        from rtrade.ml.model_io import load_model

        self._model = load_model(path)
        logger.info("meta-labeler loaded", path=str(path))
