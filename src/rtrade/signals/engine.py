"""Signal engine -- orchestrates strategy -> confluence -> levels -> candidate (PLAN 8).

This is the main deterministic pipeline. For each instrument x timeframe,
it runs the active strategy, computes confluence, validates levels, applies
risk sizing, and produces a frozen SignalCandidate.

In P1 (no LLM), the candidate goes directly to guardrails.
In P2+, it would be passed to the LLM pipeline first.
"""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

import pandas as pd
import structlog

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Timeframe
from rtrade.core.timeutil import timeframe_duration
from rtrade.indicators.structure import GapZone, SRLevel
from rtrade.signals.confluence import ConfluenceContext, compute_confluence
from rtrade.signals.edge_quality import EdgeQualityConfig, assess_edge_quality
from rtrade.signals.levels import validate_and_round_levels
from rtrade.signals.schemas import SignalCandidate
from rtrade.strategies.base import Strategy, StrategyConfig

logger = structlog.get_logger(__name__)


def generate_candidate(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    instrument: InstrumentConfig,
    df_1h: pd.DataFrame,
    df_4h: pd.DataFrame | None,
    sr_levels: list[SRLevel],
    gap_zones: list[GapZone],
    *,
    has_high_impact_event: bool = False,
    session_active: bool = True,
    funding_extreme: bool = False,
    risk_pct: float = 1.0,
    equity: float = 10_000.0,
    rr_min: float = 1.5,
    confluence_min_score: int = 60,
    valid_bars: int = 6,
    timeframe: Timeframe = Timeframe.H1,
    spread: float | None = None,
    edge_quality_enabled: bool = True,
    edge_quality_config: EdgeQualityConfig | None = None,
) -> SignalCandidate | None:
    """Run the full deterministic signal generation pipeline.

    Returns a frozen SignalCandidate if all conditions are met, or None.
    """
    if df_1h.empty:
        return None

    # 1. Add strategy-specific indicators.
    df = strategy.populate_indicators(df_1h.copy(), strategy_cfg)

    # 2. Check for entry signal.
    intent = strategy.entry_signal(df)
    if intent is None:
        return None

    logger.info(
        "entry signal detected",
        strategy=strategy.name,
        symbol=instrument.symbol,
        action=intent.action.value,
        reason=intent.reason,
    )

    # 3. Compute levels.
    try:
        raw_levels = strategy.custom_entry_price(df, intent)
    except (ValueError, IndexError) as exc:
        logger.warning("level computation failed", error=str(exc))
        return None

    # 4. Validate and round levels.
    levels = validate_and_round_levels(
        raw_levels,
        intent.action,
        instrument.pip_size,
        rr_min=rr_min,
    )
    if levels is None:
        logger.info("levels failed validation, discarding candidate")
        return None

    # 5. Confirm signal (strategy-specific sanity check).
    if not strategy.confirm_signal(df, levels):
        logger.info("strategy confirm_signal rejected candidate")
        return None

    # 6. Reject adverse-selection environments before spending confluence/LLM budget.
    if edge_quality_enabled:
        edge_report = assess_edge_quality(
            df,
            intent.action,
            levels.entry_limit,
            spread=spread,
            config=edge_quality_config,
        )
        if not edge_report.passed:
            logger.info(
                "edge quality rejected candidate",
                score=edge_report.score,
                failures=[f"{f.code}: {f.reason}" for f in edge_report.failures],
                metrics=edge_report.metrics,
            )
            return None

    # 7. Compute confluence.
    atr = float(df.iloc[-1].get("atr", 0))
    ctx = ConfluenceContext(
        df_1h=df,
        df_4h=df_4h,
        action=intent.action,
        sr_levels=sr_levels,
        gap_zones=gap_zones,
        has_high_impact_event=has_high_impact_event,
        session_active=session_active,
        funding_extreme=funding_extreme,
        atr=atr,
    )
    breakdown = compute_confluence(ctx, levels.entry_limit)

    if breakdown.total < confluence_min_score:
        logger.info(
            "confluence below threshold",
            score=breakdown.total,
            min=confluence_min_score,
        )
        return None

    # 8. Position sizing.
    sl_dist = abs(levels.entry_limit - levels.stop_loss)
    if sl_dist == 0:
        return None
    risk_amount = equity * (risk_pct / 100)
    position_size = risk_amount / sl_dist
    # Round to reasonable precision.
    position_size = round(position_size, 4)
    if position_size <= 0:
        return None

    # 9. Compute valid_until (bar close + valid_bars × timeframe).
    tf = timeframe
    raw_ts = pd.Timestamp(df.index[-1]).to_pydatetime()
    bar_ts = raw_ts if raw_ts.tzinfo is not None else raw_ts.replace(tzinfo=UTC)
    bar_close = bar_ts + timeframe_duration(tf)
    valid_until = bar_close + valid_bars * timeframe_duration(tf)

    # 10. Build frozen candidate.
    now = datetime.now(UTC)
    candidate = SignalCandidate(
        candidate_id=f"cand_{uuid.uuid4().hex[:12]}",
        symbol=instrument.symbol,
        timeframe=tf,
        strategy=strategy.name,
        action=intent.action,
        levels=levels,
        confluence_score=breakdown.total,
        confluence_breakdown=breakdown,
        risk_pct=risk_pct,
        position_size=position_size,
        valid_until=valid_until,
        bar_ts=bar_ts,
        created_at=now,
    )

    logger.info(
        "signal candidate generated",
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        action=candidate.action.value,
        entry=levels.entry_limit,
        sl=levels.stop_loss,
        tp=levels.take_profit,
        confluence=breakdown.total,
    )

    return candidate
