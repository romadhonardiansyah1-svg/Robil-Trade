"""Unit tests for deterministic edge-quality filtering."""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.signals.edge_quality import assess_edge_quality


def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=len(rows), freq="1h")
    return pd.DataFrame(rows, index=index)


def _clean_rows(n: int = 24) -> list[dict[str, float]]:
    rows = []
    for i in range(n):
        base = 100.0 + i * 0.1
        rows.append(
            {
                "open": base,
                "high": base + 1.2,
                "low": base - 0.2,
                "close": base + 1.0,
                "atr": 2.0,
                "atr_percentile": 50.0,
                "volume": 1000.0,
            }
        )
    return rows


def test_clean_buy_setup_passes() -> None:
    df = _frame(_clean_rows())

    report = assess_edge_quality(df, Action.BUY, entry_limit=102.8, spread=0.05)

    assert report.passed
    assert report.score == 100
    assert report.failures == ()


def test_high_spread_rejected() -> None:
    df = _frame(_clean_rows())

    report = assess_edge_quality(df, Action.BUY, entry_limit=102.8, spread=0.5)

    assert not report.passed
    assert any(f.code == "EQ-02" for f in report.failures)


def test_opposing_wick_rejected_for_buy() -> None:
    rows = _clean_rows()
    rows[-1] = {
        "open": 100.0,
        "high": 103.0,
        "low": 99.9,
        "close": 100.2,
        "atr": 2.0,
        "atr_percentile": 50.0,
        "volume": 1000.0,
    }
    df = _frame(rows)

    report = assess_edge_quality(df, Action.BUY, entry_limit=100.1)

    assert not report.passed
    assert any(f.code == "EQ-05" for f in report.failures)


def test_volatility_shock_rejected() -> None:
    rows = _clean_rows()
    rows[-1]["atr_percentile"] = 99.0
    df = _frame(rows)

    report = assess_edge_quality(df, Action.SELL, entry_limit=103.3)

    assert not report.passed
    assert any(f.code == "EQ-04" for f in report.failures)


def test_missing_volume_does_not_reject() -> None:
    df = _frame(_clean_rows()).drop(columns=["volume"])

    report = assess_edge_quality(df, Action.BUY, entry_limit=102.8)

    assert report.passed
    assert "volume_ratio" not in report.metrics
