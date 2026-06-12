"""REST API routes for health, signals, calibration, metrics, and manual scan."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import select

from rtrade.core.config import AppConfig
from rtrade.core.constants import SignalStatus, Timeframe
from rtrade.monitoring.healthcheck import HealthChecker
from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.models import Signal
from rtrade.persistence.repositories import SignalRepo
from rtrade.pipeline import run_scan

router = APIRouter()

_last_scan_time: float = 0.0


@router.get("/health")
async def health() -> dict[str, object]:
    """Run system health checks."""
    cfg = AppConfig.load()
    result = await HealthChecker(
        db_url=cfg.secrets.database_url,
        redis_url=cfg.secrets.redis_url,
        litellm_url=cfg.secrets.litellm_base_url,
    ).run_all()
    return result.to_dict()


@router.get("/signals")
async def list_signals(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """List recent signals."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            signals = await SignalRepo(session).recent(limit)
            return {
                "signals": [_serialize_signal(s, include_payload=False) for s in signals],
                "total": len(signals),
                "limit": limit,
            }
    finally:
        await engine.dispose()


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict[str, object]:
    """Get signal detail including payload."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            signal = await SignalRepo(session).get(signal_id)
            if signal is None:
                raise HTTPException(status_code=404, detail=f"signal {signal_id} not found")
            return _serialize_signal(signal, include_payload=True)
    finally:
        await engine.dispose()


@router.get("/calibration")
async def calibration() -> dict[str, object]:
    """Calibration metrics over the last 30 days."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    since = datetime.now(UTC) - timedelta(days=30)
    try:
        async with factory() as session:
            result = await session.execute(select(Signal).where(Signal.bar_ts >= since))
            signals = list(result.scalars().all())
            resolved = [s for s in signals if s.outcome_r is not None]
            wins = [s for s in resolved if s.outcome_r is not None and s.outcome_r > 0]
            abstained = [s for s in signals if s.status == SignalStatus.ABSTAINED.value]
            expectancy = (
                sum(float(s.outcome_r or 0) for s in resolved) / len(resolved) if resolved else None
            )
            return {
                "period_days": 30,
                "total_signals": len(signals),
                "resolved": len(resolved),
                "win_rate": len(wins) / len(resolved) if resolved else None,
                "expectancy": expectancy,
                "abstain_rate": len(abstained) / len(signals) if signals else None,
                "confidence_buckets": _confidence_buckets(signals),
            }
    finally:
        await engine.dispose()


@router.post("/scan")
async def trigger_scan(
    authorization: Annotated[str | None, Header()] = None,
    symbol: Annotated[str, Query(min_length=3)] = "XAUUSD",
    timeframe: Annotated[Timeframe, Query()] = Timeframe.H1,
) -> dict[str, object]:
    """Run a manual scan immediately."""
    global _last_scan_time

    cfg = AppConfig.load()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not cfg.secrets.api_auth_token:
        raise HTTPException(status_code=503, detail="API_AUTH_TOKEN is not configured")
    if token != cfg.secrets.api_auth_token:
        raise HTTPException(status_code=403, detail="invalid bearer token")

    now = time.time()
    if now - _last_scan_time < 60:
        raise HTTPException(status_code=429, detail="rate limit: max 1 scan per minute")
    _last_scan_time = now

    result = await run_scan(symbol.upper(), timeframe, config=cfg)
    return {
        "status": result.status,
        "signal_id": result.signal_id,
        "failures": result.failures,
        "detail": result.detail,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/metrics")
async def prometheus_metrics() -> dict[str, object]:
    """Return simple signal counters."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(select(Signal.status))
            statuses = [str(s) for s in result.scalars().all()]
            return {
                "signals_published": statuses.count(SignalStatus.PUBLISHED.value),
                "signals_rejected": statuses.count(SignalStatus.REJECTED.value),
                "signals_abstained": statuses.count(SignalStatus.ABSTAINED.value),
            }
    finally:
        await engine.dispose()


def _serialize_signal(signal: Signal, *, include_payload: bool) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "instrument_id": signal.instrument_id,
        "timeframe": signal.timeframe,
        "strategy": signal.strategy,
        "action": signal.action,
        "status": signal.status,
        "entry_limit": _decimal(signal.entry_limit),
        "stop_loss": _decimal(signal.stop_loss),
        "take_profit": _decimal(signal.take_profit),
        "risk_pct": _decimal(signal.risk_pct),
        "position_size": _decimal(signal.position_size),
        "confluence_score": signal.confluence_score,
        "confidence": _decimal(signal.confidence),
        "bar_ts": signal.bar_ts.isoformat(),
        "valid_until": signal.valid_until.isoformat() if signal.valid_until else None,
        "published_at": signal.published_at.isoformat() if signal.published_at else None,
        "resolved_at": signal.resolved_at.isoformat() if signal.resolved_at else None,
        "outcome_r": _decimal(signal.outcome_r),
        "payload": signal.payload if include_payload else None,
    }


def _decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _confidence_buckets(signals: list[Signal]) -> dict[str, int]:
    buckets = {"0.00-0.55": 0, "0.55-0.70": 0, "0.70-0.85": 0, "0.85-1.00": 0}
    for signal in signals:
        if signal.confidence is None:
            continue
        conf = float(signal.confidence)
        if conf < 0.55:
            buckets["0.00-0.55"] += 1
        elif conf < 0.70:
            buckets["0.55-0.70"] += 1
        elif conf < 0.85:
            buckets["0.70-0.85"] += 1
        else:
            buckets["0.85-1.00"] += 1
    return buckets


# ---- W10: Analytics helpers (pure, unit-testable) ----


def _aggregate_exits(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate virtual exit results across payloads (W10)."""
    policy_sums: dict[str, list[float]] = {}
    for p in payloads:
        ve = p.get("virtual_exits")
        if not ve or not isinstance(ve, dict):
            continue
        for policy_name, result in ve.items():
            if not isinstance(result, dict) or "outcome_r" not in result:
                continue
            policy_sums.setdefault(policy_name, []).append(float(result["outcome_r"]))
    return {
        policy: {
            "avg_r": round(sum(vals) / len(vals), 4) if vals else 0.0,
            "n": len(vals),
        }
        for policy, vals in policy_sums.items()
    }


def _aggregate_excursion(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate MAE/MFE excursion analytics per outcome (W10)."""
    import numpy as np

    winners_mae: list[float] = []
    winners_mfe: list[float] = []
    losers_mae: list[float] = []
    losers_mfe: list[float] = []
    for p in payloads:
        exc = p.get("excursion")
        outcome_r = p.get("outcome_r")
        if not exc or outcome_r is None:
            continue
        mae = float(exc.get("mae_r", 0))
        mfe = float(exc.get("mfe_r", 0))
        if float(outcome_r) > 0:
            winners_mae.append(mae)
            winners_mfe.append(mfe)
        else:
            losers_mae.append(mae)
            losers_mfe.append(mfe)
    result: dict[str, Any] = {
        "winners": {
            "n": len(winners_mae),
            "avg_mae_r": round(sum(winners_mae) / len(winners_mae), 4) if winners_mae else None,
            "avg_mfe_r": round(sum(winners_mfe) / len(winners_mfe), 4) if winners_mfe else None,
        },
        "losers": {
            "n": len(losers_mae),
            "avg_mae_r": round(sum(losers_mae) / len(losers_mae), 4) if losers_mae else None,
            "avg_mfe_r": round(sum(losers_mfe) / len(losers_mfe), 4) if losers_mfe else None,
        },
    }
    if winners_mae:
        result["suggested_sl_review"] = round(float(np.percentile(winners_mae, 90)), 4)
    return result


def _aggregate_failures(payloads: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate coroner failure modes (W10)."""
    modes: dict[str, int] = {}
    for p in payloads:
        cor = p.get("coroner")
        if not cor or not isinstance(cor, dict):
            continue
        mode = cor.get("failure_mode", "unknown")
        modes[mode] = modes.get(mode, 0) + 1
    return modes


# ---- W10: Analytics API routes ----


@router.get("/analytics/exits")
async def analytics_exits() -> dict[str, Any]:
    """Virtual exit policy comparison (W10)."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(select(Signal).where(Signal.outcome_r.is_not(None)))
            signals = list(result.scalars().all())
            payloads = [s.payload or {} for s in signals]
            return _aggregate_exits(payloads)
    finally:
        await engine.dispose()


@router.get("/analytics/excursion")
async def analytics_excursion() -> dict[str, Any]:
    """MAE/MFE excursion analytics (W10)."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(select(Signal).where(Signal.outcome_r.is_not(None)))
            signals = list(result.scalars().all())
            payloads = [
                {**(s.payload or {}), "outcome_r": float(s.outcome_r)}
                for s in signals
                if s.outcome_r is not None
            ]
            return _aggregate_excursion(payloads)
    finally:
        await engine.dispose()


@router.get("/analytics/failures")
async def analytics_failures() -> dict[str, Any]:
    """Coroner failure mode distribution (W10)."""
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            result = await session.execute(
                select(Signal).where(
                    Signal.status == "SL_HIT",
                    Signal.outcome_r.is_not(None),
                )
            )
            signals = list(result.scalars().all())
            payloads = [s.payload or {} for s in signals]
            return _aggregate_failures(payloads)
    finally:
        await engine.dispose()
