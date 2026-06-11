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
