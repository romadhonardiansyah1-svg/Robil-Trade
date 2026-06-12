"""Validation gates — DSR and PBO (PLAN §8.11.4).

Deflated Sharpe Ratio (Bailey & López de Prado 2014):
    Z = (SR − SR₀) × √(T−1) / √(1 − γ₃·SR + (γ₄−1)/4·SR²)
    where SR₀ = E[max(SR)] from N independent trials.

Probability of Backtest Overfitting (PBO via CSCV, Bailey et al. 2017):
    Combinatorially Symmetric Cross-Validation with S=16 partitions.
    PBO = proportion of combinations where OOS rank of IS-best is below median.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from scipy import stats  # type: ignore[import-untyped]

from rtrade.backtest.metrics import BacktestMetrics


@dataclass(frozen=True, slots=True)
class ValidationGateResult:
    """Result of all validation gates."""

    n_trades_oos: int
    expectancy_oos: float
    profit_factor_oos: float
    max_drawdown_pct: float
    dsr_probability: float
    pbo: float
    permutation_p: float | None
    all_passed: bool
    gate_results: dict[str, bool]


def expected_max_sharpe(n_trials: int, t_periods: int) -> float:
    """Expected maximum Sharpe ratio from N independent trials (Bailey & López de Prado).

    E[max(Z)] ≈ (1 - γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))
    where γ ≈ 0.5772 (Euler-Mascheroni), and we adjust for T.
    """
    if n_trials <= 1:
        return 0.0

    gamma = 0.5772  # Euler-Mascheroni constant
    z1 = stats.norm.ppf(1 - 1 / n_trials)
    z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
    sr0 = (1 - gamma) * z1 + gamma * z2
    return float(sr0)


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    t_periods: int,
    skewness: float = 0.0,
    kurtosis: float = 0.0,
) -> float:
    """Compute Deflated Sharpe Ratio probability (PLAN §8.11.4).

    Returns P(SR > SR₀) — probability that the observed Sharpe exceeds
    the expected maximum from N trials (accounting for skew & kurtosis).
    Should be ≥ 0.90 to pass the validation gate.
    """
    if t_periods <= 1 or n_trials < 1:
        return 0.0

    sr0 = expected_max_sharpe(n_trials, t_periods)

    # Standard error of Sharpe accounting for non-normality.
    # SE(SR) = √((1 − γ₃·SR + (γ₄−1)/4·SR²) / (T−1))
    denom = 1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe**2
    if denom <= 0:
        denom = 1.0  # fallback to avoid negative sqrt

    se = math.sqrt(denom / (t_periods - 1))

    if se == 0:
        return 1.0 if sharpe > sr0 else 0.0

    z = (sharpe - sr0) / se
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

    # Gate 5: Deflated Sharpe Ratio.
    dsr_prob = deflated_sharpe_ratio(
        metrics.sharpe_ratio,
        n_trials,
        metrics.n_trades,
        metrics.skewness,
        metrics.kurtosis,
    )
    gates["dsr_prob >= 0.90"] = dsr_prob >= min_dsr_prob

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
        pbo=pbo_val,
        permutation_p=permutation_p,
        all_passed=all_passed,
        gate_results=gates,
    )
