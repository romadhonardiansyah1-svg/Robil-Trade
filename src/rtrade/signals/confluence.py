"""Confluence scorer -- 0-100 composite quality score (PLAN 8.6).

Components (weights from config/strategies/*.yaml):
    trend      (25): Trend alignment on 1H+4H
    momentum   (20): MACD histogram direction + RSI recovery
    structure  (20): Entry near S/R or gap zone
    volume     (15): Volume > 1.2x SMA20 (redistributed if unavailable)
    macro      (20): No high-impact events + session active + funding OK

Candidates proceed only if score >= confluence_min_score (default 60).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from rtrade.core.constants import Action
from rtrade.indicators.structure import GapZone, SRLevel
from rtrade.signals.schemas import ConfluenceBreakdown


@dataclass
class ConfluenceContext:
    """All the data needed to score confluence."""

    df_1h: pd.DataFrame
    df_4h: pd.DataFrame | None  # None if 4H data not available
    action: Action
    sr_levels: list[SRLevel]
    gap_zones: list[GapZone]
    has_high_impact_event: bool
    session_active: bool
    funding_extreme: bool  # crypto: funding rate extreme for this direction
    atr: float


def score_trend(
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame | None,
    action: Action,
    max_score: int = 25,
) -> int:
    """Score trend alignment across timeframes."""
    if df_1h.empty:
        return 0

    last_1h = df_1h.iloc[-1]
    score = 0
    total_checks = 4

    close = float(last_1h["close"])
    ema200 = float(last_1h.get("ema200", 0))
    ema50 = float(last_1h.get("ema50", 0))
    adx = float(last_1h.get("adx", 0))

    if action == Action.BUY:
        if close > ema200:
            score += 1
        if ema50 > ema200:
            score += 1
    else:
        if close < ema200:
            score += 1
        if ema50 < ema200:
            score += 1

    if adx >= 20:
        score += 1

    # 4H confirmation.
    if df_4h is not None and not df_4h.empty:
        last_4h = df_4h.iloc[-1]
        ema200_4h = float(last_4h.get("ema200", 0))
        close_4h = float(last_4h["close"])
        if (action == Action.BUY and close_4h > ema200_4h) or (
            action == Action.SELL and close_4h < ema200_4h
        ):
            score += 1
    else:
        total_checks -= 1

    if total_checks == 0:
        return 0
    return round(score / total_checks * max_score)


def score_momentum(
    df_1h: pd.DataFrame,
    action: Action,
    max_score: int = 20,
) -> int:
    """Score momentum (MACD histogram direction + RSI recovery)."""
    if df_1h.empty:
        return 0

    last = df_1h.iloc[-1]
    score = 0

    macd_hist = float(last.get("macd_hist", 0))
    rsi = float(last.get("rsi", 50))

    # MACD histogram in the right direction.
    if (action == Action.BUY and macd_hist > 0) or (action == Action.SELL and macd_hist < 0):
        score += 1

    # RSI leaving oversold/overbought (recovery).
    if (action == Action.BUY and 40 <= rsi <= 65) or (action == Action.SELL and 35 <= rsi <= 60):
        score += 1

    return round(score / 2 * max_score)


def score_structure(
    entry: float,
    sr_levels: list[SRLevel],
    gap_zones: list[GapZone],
    action: Action,
    atr: float,
    max_score: int = 20,
) -> int:
    """Score proximity to S/R levels or gap zones."""
    if atr <= 0:
        return 0

    score = 0
    tolerance = 0.5 * atr

    # Check proximity to S/R.
    for level in sr_levels:
        if abs(entry - level.price) <= tolerance:
            if action == Action.BUY and not level.is_resistance:
                score += level.strength  # support confluence
            elif action == Action.SELL and level.is_resistance:
                score += level.strength
            break  # count nearest only

    # Check if entry aligns with a gap zone.
    for gap in gap_zones:
        gap_dir_match = (action == Action.BUY and gap.direction == "bullish") or (
            action == Action.SELL and gap.direction == "bearish"
        )
        if gap_dir_match and gap.low <= entry <= gap.high:
            score += 2
            break

    # Normalise to max_score (rough: 3+ points = full score).
    return min(max_score, round(score / 3 * max_score))


def score_volume(
    df_1h: pd.DataFrame,
    max_score: int = 15,
) -> int:
    """Score volume (trigger bar >= 1.2x SMA20 volume)."""
    if "volume" not in df_1h.columns:
        return 0

    vol = df_1h["volume"].astype(float)
    if vol.sum() == 0:
        return 0  # no volume data (FX via TwelveData may lack tick volume)

    sma20_vol = vol.rolling(20, min_periods=1).mean()
    last_vol = float(vol.iloc[-1])
    last_sma = float(sma20_vol.iloc[-1])

    if last_sma == 0:
        return 0
    ratio = last_vol / last_sma

    if ratio >= 1.5:
        return max_score
    if ratio >= 1.2:
        return round(max_score * 0.7)
    if ratio >= 1.0:
        return round(max_score * 0.4)
    return 0


def score_macro(
    has_high_impact_event: bool,
    session_active: bool,
    funding_extreme: bool,
    max_score: int = 20,
) -> int:
    """Score macro context (events, session, funding)."""
    score = max_score

    if has_high_impact_event:
        score -= 10  # heavy penalty
    if not session_active:
        score -= 5
    if funding_extreme:
        score -= 5

    return max(0, score)


def compute_confluence(ctx: ConfluenceContext, entry: float) -> ConfluenceBreakdown:
    """Compute the full confluence score from context."""
    # If volume data is missing, redistribute its weight proportionally.
    has_volume = "volume" in ctx.df_1h.columns and ctx.df_1h["volume"].astype(float).sum() > 0

    if has_volume:
        vol_score = score_volume(ctx.df_1h, max_score=15)
    else:
        vol_score = 0  # redistributed: other components get proportionally more

    trend = score_trend(ctx.df_1h, ctx.df_4h, ctx.action, max_score=25)
    momentum = score_momentum(ctx.df_1h, ctx.action, max_score=20)
    structure = score_structure(
        entry, ctx.sr_levels, ctx.gap_zones, ctx.action, ctx.atr, max_score=20
    )
    macro = score_macro(
        ctx.has_high_impact_event, ctx.session_active, ctx.funding_extreme, max_score=20
    )

    # If no volume data, redistribute 15 points proportionally.
    if not has_volume:
        max_other = 25 + 20 + 20 + 20  # 85
        if max_other > 0:
            boost_factor = 100 / max_other
            trend = min(25, round(trend * boost_factor * 25 / 100))
            momentum = min(20, round(momentum * boost_factor * 20 / 100))
            structure = min(20, round(structure * boost_factor * 20 / 100))
            macro = min(20, round(macro * boost_factor * 20 / 100))

    return ConfluenceBreakdown(
        trend=min(25, trend),
        momentum=min(20, momentum),
        structure=min(20, structure),
        volume=min(15, vol_score),
        macro=min(20, macro),
    )
