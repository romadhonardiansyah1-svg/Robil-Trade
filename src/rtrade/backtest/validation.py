"""Validation gates — DSR and PBO (PLAN §8.11.4).

Deflated Sharpe Ratio (Bailey & López de Prado 2014, "The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality"):

    All quantities are in PER-TRADE units. Let SR be the observed per-trade
    Sharpe, T the number of trades, g3 the skewness and ek the EXCESS kurtosis.

        sigma_sr = sqrt((1 - g3*SR + (ek + 2)/4 * SR**2) / (T - 1))   # SE of SR
        z_max(N) = (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))            # dimensionless
        sr0      = z_max(N) * sigma_sr                                # deflated threshold
        DSR      = Φ((SR - sr0) / sigma_sr) = Φ(SR/sigma_sr - z_max(N))

    γ ≈ 0.5772 (Euler-Mascheroni). DSR depends correctly on T (via sigma_sr),
    N (via z_max), skewness and kurtosis. Should be ≥ 0.90 to pass the gate.

Probability of Backtest Overfitting (PBO via CSCV, Bailey et al. 2017):
    Combinatorially Symmetric Cross-Validation with S=16 partitions.
    PBO = proportion of combinations where OOS rank of IS-best is below median.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import math

import numpy as np
from scipy import stats  # type: ignore[import-untyped]
import structlog

from rtrade.backtest.metrics import BacktestMetrics

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ValidationGateResult:
    """Result of all validation gates."""

    n_trades_oos: int
    expectancy_oos: float
    profit_factor_oos: float
    max_drawdown_pct: float
    dsr_probability: float
    dsr_undeflated: bool  # True when n_trials < 2 (no overfitting deflation applied)
    pbo: float
    permutation_p: float | None
    all_passed: bool
    gate_results: dict[str, bool]


def expected_max_sharpe(n_trials: int) -> float:
    """Expected maximum of ``n_trials`` independent standard-normal draws.

    Bailey & López de Prado (2014), eq. for E[max]:

        z_max(N) ≈ (1 - γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))

    with γ ≈ 0.5772 (Euler-Mascheroni). This is a DIMENSIONLESS z-score
    (NOT a Sharpe), monotonically increasing in N, used to deflate the Sharpe
    threshold. Returns 0.0 for N <= 1 (a single trial cannot be inflated by
    selection). Depends ONLY on ``n_trials``.
    """
    if n_trials <= 1:
        return 0.0

    gamma = 0.5772  # Euler-Mascheroni constant
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    return float((1 - gamma) * z1 + gamma * z2)


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    t_periods: int,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
) -> float:
    """Deflated Sharpe Ratio probability (Bailey & López de Prado 2014).

    ``sharpe`` MUST be the per-trade Sharpe (un-annualized) and ``t_periods``
    the number of trades T. ``kurtosis`` is EXCESS kurtosis (normal == 0).

    Returns P(SR > SR₀) — the probability the observed per-trade Sharpe exceeds
    the deflated expected maximum from N trials, accounting for sample size T,
    skewness and (excess) kurtosis. Should be ≥ 0.90 to pass the gate.
    """
    if t_periods <= 1 or n_trials < 1:
        return 0.0

    # Standard error of the Sharpe estimate (Mertens / Lo; BLdP 2014).
    #   sigma_sr = sqrt((1 - g3*SR + (ek + 2)/4 * SR**2) / (T - 1))
    # NOTE: ek is EXCESS kurtosis. The classic term is (g4 - 1)/4 with NON-excess
    # kurtosis g4; since g4 = ek + 3, the correct excess form is (ek + 2)/4.
    denom = 1 - skewness * sharpe + (kurtosis + 2) / 4 * sharpe**2
    if denom <= 0:
        # Degenerate variance term: we cannot certify the Sharpe -> fail safe.
        return 0.0

    sigma_sr = math.sqrt(denom / (t_periods - 1))

    # Deflated threshold in per-trade Sharpe units, then the DSR probability.
    z_max = expected_max_sharpe(n_trials)
    z = sharpe / sigma_sr - z_max
    return float(stats.norm.cdf(z))


def probability_of_backtest_overfitting(
    returns_matrix: np.ndarray,  # type: ignore[type-arg]
    s_partitions: int = 16,
) -> float:
    """Compute PBO via CSCV (PLAN §8.11.4, Bailey et al. 2017).

    Args:
        returns_matrix: (T, N) matrix of returns for N parameter combinations.
            T = number of time periods, N = number of trials/configurations.
        s_partitions: Number of partitions (default 16 per PLAN).

    Returns:
        PBO value (should be ≤ 0.30 to pass validation gate).
    """
    t, n = returns_matrix.shape
    if t < s_partitions or n < 2:
        return 0.0  # insufficient data

    # Split time periods into S equal-sized groups.
    partition_size = t // s_partitions
    indices = list(range(s_partitions))

    # Number of ways to choose S/2 partitions from S for training.
    half = s_partitions // 2
    combos = list(combinations(indices, half))

    overfit_count = 0

    for combo in combos:
        train_set = set(combo)
        test_set = set(indices) - train_set

        # Build train and test returns.
        train_mask = np.zeros(t, dtype=bool)
        test_mask = np.zeros(t, dtype=bool)

        for p in train_set:
            start = p * partition_size
            end = start + partition_size
            train_mask[start:end] = True

        for p in test_set:
            start = p * partition_size
            end = start + partition_size
            test_mask[start:end] = True

        train_returns = returns_matrix[train_mask]
        test_returns = returns_matrix[test_mask]

        if len(train_returns) == 0 or len(test_returns) == 0:
            continue

        # Find best configuration in-sample.
        is_performance = np.mean(train_returns, axis=0)
        best_is = np.argmax(is_performance)

        # Check OOS rank of IS-best.
        oos_performance = np.mean(test_returns, axis=0)
        oos_rank = np.sum(oos_performance > oos_performance[best_is])
        median_rank = n / 2

        if oos_rank >= median_rank:
            overfit_count += 1

    pbo = overfit_count / len(combos) if combos else 0.0
    return pbo


def run_validation_gates(
    metrics: BacktestMetrics,
    n_trials: int = 1,
    *,
    min_trades: int = 100,
    min_expectancy: float = 0.0,
    min_profit_factor: float = 1.15,
    max_drawdown_pct: float = 25.0,
    min_dsr_prob: float = 0.90,
    max_pbo: float = 0.30,
    pbo_value: float | None = None,
    permutation_p: float | None = None,
) -> ValidationGateResult:
    """Run all validation gates on backtest metrics (PLAN §8.11.4)."""
    gates: dict[str, bool] = {}

    # Gate 1: Minimum trades.
    gates["n_trades_oos >= 100"] = metrics.n_trades >= min_trades

    # Gate 2: Positive expectancy after costs.
    gates["expectancy_oos > 0"] = metrics.expectancy > min_expectancy

    # Gate 3: Profit factor.
    gates["profit_factor >= 1.15"] = metrics.profit_factor >= min_profit_factor

    # Gate 4: Max drawdown.
    gates["max_drawdown <= 25%"] = metrics.max_drawdown_pct <= max_drawdown_pct

    # Gate 5: Deflated Sharpe Ratio (per-trade units — NOT the annualized value).
    dsr_prob = deflated_sharpe_ratio(
        metrics.sharpe_per_trade,
        n_trials,
        metrics.n_trades,
        metrics.skewness,
        metrics.kurtosis,
    )
    gates["dsr_prob >= 0.90"] = dsr_prob >= min_dsr_prob

    # When fewer than 2 configurations were tried the deflation term z_max is 0,
    # so the DSR collapses to the (undeflated) Probabilistic Sharpe Ratio vs 0.
    # That is mathematically valid but means a single-config run is NOT certified
    # against selection bias / overfitting — surface it loudly so it cannot
    # silently look "validated against overfitting".
    dsr_undeflated = n_trials < 2
    if dsr_undeflated:
        logger.warning(
            "dsr_gate_undeflated",
            n_trials=n_trials,
            dsr_probability=dsr_prob,
            reason="n_trials < 2 -> no overfitting deflation applied (DSR == PSR vs 0)",
        )

    # Gate 6: PBO.
    pbo_val = pbo_value if pbo_value is not None else 0.0
    gates["pbo <= 0.30"] = pbo_val <= max_pbo

    # Gate 7: Permutation p-value (W7).
    if permutation_p is not None:
        gates["permutation_p <= 0.05"] = permutation_p <= 0.05

    all_passed = all(gates.values())

    return ValidationGateResult(
        n_trades_oos=metrics.n_trades,
        expectancy_oos=metrics.expectancy,
        profit_factor_oos=metrics.profit_factor,
        max_drawdown_pct=metrics.max_drawdown_pct,
        dsr_probability=dsr_prob,
        dsr_undeflated=dsr_undeflated,
        pbo=pbo_val,
        permutation_p=permutation_p,
        all_passed=all_passed,
        gate_results=gates,
    )
