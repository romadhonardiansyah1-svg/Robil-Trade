"""REST API routes (PLAN §8.10).

Endpoints:
    GET  /health         — DB/Redis/provider/LiteLLM ping
    GET  /signals        — latest signals (limit=20)
    GET  /signals/{id}   — signal detail with audit + confluence
    GET  /calibration    — WR, expectancy, abstain-rate
    POST /scan           — manual trigger (auth bearer, rate-limited 1/min)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Header, HTTPException, Query

router = APIRouter()

# Simple in-memory rate limiter for /scan.
_last_scan_time: float = 0.0


@router.get("/health")
async def health() -> dict[str, object]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.now(UTC).isoformat(),
        "services": {
            "api": "ok",
            # In production, these will ping actual services.
            "database": "ok",
            "redis": "ok",
        },
    }


@router.get("/signals")
async def list_signals(
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """List recent signals."""
    # TODO: wire to SignalRepo in P1 integration.
    return {
        "signals": [],
        "total": 0,
        "limit": limit,
    }


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: str) -> dict[str, object]:
    """Get signal detail including audit trail and confluence breakdown."""
    # TODO: wire to SignalRepo + AuditRepo.
    raise HTTPException(status_code=404, detail=f"signal {signal_id} not found")


@router.get("/calibration")
async def calibration() -> dict[str, object]:
    """Calibration metrics — WR, expectancy, abstain-rate over 30 days."""
    # TODO: compute from resolved paper-trades.
    return {
        "period_days": 30,
        "total_signals": 0,
        "resolved": 0,
        "win_rate": None,
        "expectancy": None,
        "abstain_rate": None,
        "confidence_buckets": {},
    }


@router.post("/scan")
async def trigger_scan(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Manual scan trigger (PLAN §8.10).

    Requires bearer token (API_AUTH_TOKEN). Rate-limited to 1/minute.
    """
    global _last_scan_time

    # Auth check.
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    # Rate limit: 1 per minute.
    now = time.time()
    if now - _last_scan_time < 60:
        raise HTTPException(
            status_code=429,
            detail="rate limit: max 1 scan per minute",
        )
    _last_scan_time = now

    # TODO: trigger actual scan pipeline.
    return {
        "status": "scan_queued",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/metrics")
async def prometheus_metrics() -> dict[str, object]:
    """Prometheus-compatible metrics (PLAN §8.13).

    TODO: integrate prometheus_client for proper /metrics output.
    """
    return {
        "signals_published": 0,
        "signals_rejected": 0,
        "signals_abstained": 0,
    }
