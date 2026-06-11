"""HMM-based regime detector (PLAN §8.3 P3).

GaussianHMM with 3 states, features:
  - Log-return
  - ATR-normalized range
  - Volume z-score

Walk-forward: 2-year rolling window, retrain weekly.
State mapping: HMM states → TREND/RANGE/CRISIS via emission means.

Only replaces rule-based if accuracy is higher on backtest data.
See ADR-013 for evaluation results.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import structlog
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

from rtrade.core.constants import Regime

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HMMRegimeState:
    """Regime classification from HMM."""

    regime: Regime
    state_id: int  # raw HMM state (0, 1, 2)
    probability: float  # posterior probability of current state
    since: datetime
    log_likelihood: float  # model fit quality


class HMMRegimeDetector:
    """HMM-based regime detector with walk-forward retraining.

    Trains a 3-state GaussianHMM on:
      - Log-returns (capturing trend direction/magnitude)
      - ATR-normalized range (volatility relative to ATR)
      - Volume z-score (participation level)

    State mapping is done by analyzing emission means:
      - Highest volatility state → CRISIS
      - Highest absolute return state (non-crisis) → TREND
      - Remaining → RANGE
    """

    def __init__(
        self,
        n_states: int = 3,
        train_window_bars: int = 5000,
        covariance_type: str = "diag",
        n_iter: int = 100,
        random_state: int = 42,
    ) -> None:
        self._n_states = n_states
        self._train_window = train_window_bars
        self._cov_type = covariance_type
        self._n_iter = n_iter
        self._random_state = random_state

        self._model: GaussianHMM | None = None
        self._scaler: StandardScaler | None = None
        self._state_map: dict[int, Regime] = {}
        self._last_train_ts: datetime | None = None
        self._prev_state: dict[str, HMMRegimeState] = {}

    def _prepare_features(self, df: pd.DataFrame) -> np.ndarray:
        """Extract feature matrix from OHLCV DataFrame.

        Features:
          0: log-return (close-to-close)
          1: ATR-normalized range ((high-low)/ATR)
          2: volume z-score (rolling 50-bar)
        """
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)

        # Log return.
        log_ret = np.log(close / close.shift(1))

        # ATR-normalized range.
        atr = df.get("atr")
        if atr is not None:
            atr = atr.astype(float)
            norm_range = (high - low) / atr.replace(0, np.nan)
        else:
            norm_range = (high - low) / close  # fallback

        # Volume z-score.
        vol = df.get("volume")
        if vol is not None:
            vol = vol.astype(float)
            vol_mean = vol.rolling(50, min_periods=10).mean()
            vol_std = vol.rolling(50, min_periods=10).std()
            vol_z = (vol - vol_mean) / vol_std.replace(0, np.nan)
        else:
            vol_z = pd.Series(0.0, index=df.index)

        features = pd.DataFrame(
            {
                "log_ret": log_ret,
                "norm_range": norm_range,
                "vol_z": vol_z,
            }
        ).dropna()

        return features.values

    def train(self, df: pd.DataFrame) -> float:
        """Train the HMM model on historical data.

        Args:
            df: OHLCV DataFrame with at least `close`, `high`, `low`.
                Optionally `atr` and `volume`.

        Returns:
            Log-likelihood of the fitted model.
        """
        # Use training window.
        train_df = df.tail(self._train_window)
        X_raw = self._prepare_features(train_df)

        if len(X_raw) < 200:
            raise ValueError(
                f"insufficient data for HMM training: {len(X_raw)} samples (need ≥200)"
            )

        # Standardize features for numerical stability.
        scaler = StandardScaler()
        X = scaler.fit_transform(X_raw)
        self._scaler = scaler

        model = GaussianHMM(
            n_components=self._n_states,
            covariance_type=self._cov_type,
            n_iter=self._n_iter,
            random_state=self._random_state,
            min_covar=1e-3,
        )
        model.fit(X)

        self._model = model
        self._state_map = self._map_states(model)
        self._last_train_ts = datetime.now(UTC)

        ll = float(model.score(X))
        logger.info(
            "HMM trained",
            n_samples=len(X),
            log_likelihood=f"{ll:.2f}",
            state_map={k: v.value for k, v in self._state_map.items()},
        )
        return ll

    def _map_states(self, model: GaussianHMM) -> dict[int, Regime]:
        """Map HMM states to TREND/RANGE/CRISIS via emission means.

        Logic:
          - Highest norm_range mean → CRISIS (highest volatility)
          - Highest abs(log_ret) mean among non-crisis → TREND
          - Remaining → RANGE
        """
        means = model.means_  # shape (n_states, n_features)
        # Feature indices: 0=log_ret, 1=norm_range, 2=vol_z

        # Find CRISIS state: highest volatility (norm_range).
        crisis_state = int(np.argmax(means[:, 1]))

        # Among remaining, highest abs(log_ret) → TREND.
        remaining = [i for i in range(self._n_states) if i != crisis_state]

        if len(remaining) >= 2:
            trend_state = max(remaining, key=lambda i: abs(means[i, 0]))
            range_states = [i for i in remaining if i != trend_state]
            range_state = range_states[0]
        else:
            trend_state = remaining[0]
            range_state = crisis_state  # degenerate case

        return {
            crisis_state: Regime.CRISIS,
            trend_state: Regime.TREND,
            range_state: Regime.RANGE,
        }

    def classify(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        now: datetime | None = None,
    ) -> HMMRegimeState:
        """Classify current regime using trained HMM.

        Raises:
            RuntimeError: If model not trained yet.
        """
        if self._model is None or self._scaler is None:
            raise RuntimeError("HMM model not trained. Call train() first.")

        X_raw = self._prepare_features(df)
        if len(X_raw) == 0:
            raise ValueError("no valid features to classify")

        X = self._scaler.transform(X_raw)

        # Predict state for the last observation.
        states = self._model.predict(X)
        current_state = int(states[-1])

        # Get posterior probabilities for the last observation.
        posteriors = self._model.predict_proba(X)
        prob = float(posteriors[-1, current_state])

        regime = self._state_map.get(current_state, Regime.RANGE)

        ts = now or datetime.now(UTC)

        # Hysteresis: keep 'since' if regime unchanged.
        prev = self._prev_state.get(symbol)
        since = ts
        if prev is not None and prev.regime == regime:
            since = prev.since

        n_score = min(100, len(X))
        ll = float(self._model.score(X[-n_score:]))

        state = HMMRegimeState(
            regime=regime,
            state_id=current_state,
            probability=prob,
            since=since,
            log_likelihood=ll,
        )
        self._prev_state[symbol] = state
        return state

    def compare_with_rule_based(
        self,
        df: pd.DataFrame,
        rule_based_regimes: list[Regime],
    ) -> dict[str, Any]:
        """Compare HMM classification vs rule-based on the same data.

        Returns accuracy metrics for ADR-013 documentation.
        """
        if self._model is None or self._scaler is None:
            raise RuntimeError("HMM not trained")

        X_raw = self._prepare_features(df)
        X = self._scaler.transform(X_raw)
        if len(X) != len(rule_based_regimes):
            # Align: features drop NaN rows, so trim rule_based.
            n = min(len(X), len(rule_based_regimes))
            X = X[-n:]
            rule_based_regimes = rule_based_regimes[-n:]

        hmm_states = self._model.predict(X)
        hmm_regimes = [self._state_map.get(int(s), Regime.RANGE) for s in hmm_states]

        # Compute agreement rate.
        total = len(hmm_regimes)
        agree = sum(1 for h, r in zip(hmm_regimes, rule_based_regimes, strict=False) if h == r)

        # Per-regime accuracy.
        per_regime: dict[str, dict[str, int]] = {}
        for regime in Regime:
            rb_count = sum(1 for r in rule_based_regimes if r == regime)
            if rb_count > 0:
                hmm_correct = sum(
                    1
                    for h, r in zip(hmm_regimes, rule_based_regimes, strict=False)
                    if r == regime and h == regime
                )
                per_regime[regime.value] = {
                    "rule_based_count": rb_count,
                    "hmm_correct": hmm_correct,
                    "accuracy_pct": round(hmm_correct / rb_count * 100, 1),
                }

        return {
            "total_samples": total,
            "agreement_count": agree,
            "agreement_rate_pct": round(agree / total * 100, 1) if total > 0 else 0,
            "per_regime": per_regime,
        }

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    @property
    def last_train_ts(self) -> datetime | None:
        return self._last_train_ts
