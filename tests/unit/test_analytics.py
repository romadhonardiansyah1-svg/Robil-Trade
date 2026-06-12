"""W10: Tests for analytics aggregation helpers."""

from rtrade.delivery.api.routes import _aggregate_excursion, _aggregate_exits, _aggregate_failures


def test_aggregate_exits_by_policy() -> None:
    """Virtual exits aggregate per policy."""
    payloads = [
        {
            "virtual_exits": {
                "fixed_2r": {"outcome_r": 2.0, "hit": "TP"},
                "partial_be": {"outcome_r": 1.5, "hit": "TP"},
            }
        },
        {
            "virtual_exits": {
                "fixed_2r": {"outcome_r": -1.0, "hit": "SL"},
                "partial_be": {"outcome_r": 0.5, "hit": "BE"},
            }
        },
    ]
    result = _aggregate_exits(payloads)
    assert result["fixed_2r"]["n"] == 2
    assert result["fixed_2r"]["avg_r"] == 0.5  # (2.0 + -1.0) / 2
    assert result["partial_be"]["n"] == 2


def test_aggregate_excursion_winners_vs_losers() -> None:
    """MAE/MFE separate winners vs losers."""
    payloads = [
        {"excursion": {"mae_r": 0.3, "mfe_r": 2.1}, "outcome_r": 1.5},
        {"excursion": {"mae_r": 1.2, "mfe_r": 0.4}, "outcome_r": -1.0},
    ]
    result = _aggregate_excursion(payloads)
    assert result["winners"]["n"] == 1
    assert result["losers"]["n"] == 1
    assert result["winners"]["avg_mae_r"] == 0.3
    assert result["losers"]["avg_mae_r"] == 1.2
    assert "suggested_sl_review" in result


def test_aggregate_failures_distribution() -> None:
    """Coroner failure mode counts."""
    payloads = [
        {"coroner": {"failure_mode": "trend_reversal"}},
        {"coroner": {"failure_mode": "trend_reversal"}},
        {"coroner": {"failure_mode": "volatility_spike"}},
        {},  # no coroner — should be skipped
    ]
    result = _aggregate_failures(payloads)
    assert result["trend_reversal"] == 2
    assert result["volatility_spike"] == 1
