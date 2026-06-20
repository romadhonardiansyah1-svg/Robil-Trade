"""Metrics tests — Sharpe annualization (A3), neutral 0R trades (A12),
finite profit-factor sentinel (A13).

Statistical references: Bailey & López de Prado (2014), "The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality".
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from rtrade.backtest.metrics import PROFIT_FACTOR_CAP, compute_metrics

# A fixed R-multiple series with hand-computed statistics.
#   mean = 0.8
#   sample std (ddof=1) = sqrt(2.7) = 1.6431677
#   sharpe_per_trade = 0.8 / 1.6431677 = 0.48686450
_R = [2.0, -1.0, 2.0, -1.0, 2.0]
_EQUITY = [10_000.0, 10_200.0, 10_100.0, 10_300.0, 10_200.0, 10_400.0]


# --------------------------------------------------------------------------- #
# A3 — Sharpe annualization
# --------------------------------------------------------------------------- #
def test_sharpe_per_trade_is_raw_mean_over_std() -> None:
    """sharpe_per_trade must be the un-annualized mean(r)/std(r, ddof=1)."""
    m = compute_metrics(_R, _EQUITY)
    expected = float(np.mean(_R)) / float(np.std(_R, ddof=1))
    assert m.sharpe_per_trade == pytest.approx(expected)
    assert m.sharpe_per_trade == pytest.approx(0.48686450, abs=1e-6)


def test_sharpe_ratio_annualized_by_actual_trade_frequency() -> None:
    """With trades_per_year=50, annualized Sharpe = sharpe_per_trade * sqrt(50)."""
    m = compute_metrics(_R, _EQUITY, trades_per_year=50.0)
    assert m.sharpe_ratio == pytest.approx(m.sharpe_per_trade * math.sqrt(50.0))
    # NOT the old hard-coded sqrt(252) behaviour.
    assert m.sharpe_ratio != pytest.approx(m.sharpe_per_trade * math.sqrt(252.0))


def test_sharpe_ratio_defaults_to_n_trades_as_trades_per_year() -> None:
    """When trades_per_year is None, fall back to len(r_multiples)."""
    m = compute_metrics(_R, _EQUITY)
    assert m.sharpe_ratio == pytest.approx(m.sharpe_per_trade * math.sqrt(len(_R)))


# --------------------------------------------------------------------------- #
# A12 — 0R trades are NEUTRAL (scratch), not losses
# --------------------------------------------------------------------------- #
def test_zero_r_trade_is_neutral() -> None:
    """A 0R trade is neither a win nor a loss for win_rate/avg_loss/profit_factor."""
    m = compute_metrics([1.0, 0.0, -1.0], [10_000.0, 10_100.0, 10_100.0, 10_000.0])
    # Decisive trades = 1 win + 1 loss; the scratch trade is excluded.
    assert m.win_rate == pytest.approx(0.5)
    # avg_loss averages only strictly-negative R (the 0R is not a loss).
    assert m.avg_loss_r == pytest.approx(1.0)
    # gross_loss excludes the 0R trade -> PF = 1.0 / 1.0.
    assert m.profit_factor == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# A13 — profit factor with zero losses returns a finite sentinel cap
# --------------------------------------------------------------------------- #
def test_profit_factor_zero_losses_is_finite_cap() -> None:
    """All-wins must yield a large FINITE profit factor, not float('inf')."""
    m = compute_metrics([2.0, 2.0, 2.0], [10_000.0, 10_200.0, 10_400.0, 10_600.0])
    assert math.isfinite(m.profit_factor)
    assert m.profit_factor == pytest.approx(PROFIT_FACTOR_CAP)
