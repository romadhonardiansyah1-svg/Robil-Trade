"""Unit tests for confluence scoring."""

from __future__ import annotations

import pandas as pd

from rtrade.core.constants import Action
from rtrade.signals.confluence import ConfluenceContext, compute_confluence


def _trend_df(*, with_volume: bool) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "high": [102.0, 103.0],
            "low": [99.0, 100.0],
            "close": [101.0, 102.0],
            "ema21": [100.0, 101.0],
            "ema50": [99.0, 100.0],
            "ema200": [90.0, 91.0],
            "adx": [30.0, 30.0],
            "macd_hist": [1.0, 1.0],
            "rsi": [50.0, 50.0],
            "atr": [2.0, 2.0],
        },
        index=pd.date_range("2026-01-01", periods=2, freq="1h"),
    )
    if with_volume:
        df["volume"] = [1000.0, 3000.0]
    return df


def test_no_volume_keeps_score_on_100_point_scale() -> None:
    df = _trend_df(with_volume=False)
    ctx = ConfluenceContext(
        df_1h=df,
        df_4h=df,
        action=Action.BUY,
        sr_levels=[],
        gap_zones=[],
        has_high_impact_event=False,
        session_active=True,
        funding_extreme=False,
        atr=2.0,
    )

    score = compute_confluence(ctx, entry=101.0)

    assert score.trend == 25
    assert score.momentum == 20
    assert score.macro == 20
    assert score.volume == 11
    assert score.total == 76


def test_volume_score_uses_real_volume_when_available() -> None:
    df = _trend_df(with_volume=True)
    ctx = ConfluenceContext(
        df_1h=df,
        df_4h=df,
        action=Action.BUY,
        sr_levels=[],
        gap_zones=[],
        has_high_impact_event=False,
        session_active=True,
        funding_extreme=False,
        atr=2.0,
    )

    score = compute_confluence(ctx, entry=101.0)

    assert score.volume == 15
    assert score.total == 80
