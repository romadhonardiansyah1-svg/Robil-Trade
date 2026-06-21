"""REST API routes for health, signals, calibration, metrics, and manual scan."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hmac
import time
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rtrade.core.config import AppConfig
from rtrade.core.constants import SignalStatus, Timeframe
from rtrade.monitoring.healthcheck import HealthChecker, HealthStatus
from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.models import Signal
from rtrade.persistence.repositories import CalendarSourceHealthRepo, SignalRepo
from rtrade.pipeline import run_scan

router = APIRouter()

# S10 / C5: per-IP auth failure tracking (rate limiting).
# Insertion-ordered so we can evict the oldest tracked key (LRU) once the map
# reaches its cap — this bounds memory under a distributed/spoofed-IP flood.
_auth_failures: OrderedDict[str, list[float]] = OrderedDict()
_AUTH_FAIL_WINDOW = 60.0  # seconds
_AUTH_FAIL_LIMIT = 10  # max failures per window
_AUTH_FAIL_MAX_KEYS = 4096  # C5: hard cap on tracked keys (memory-DoS guard)


def _prune_auth_failures(now: float) -> None:
    """C5: drop any key whose timestamps have all aged out of the window.

    Without this, keys (one per distinct client IP) accumulate forever even
    after their failures expire — an unbounded-growth memory DoS.
    """
    stale = [
        ip for ip, ts in _auth_failures.items() if not any(now - t < _AUTH_FAIL_WINDOW for t in ts)
    ]
    for ip in stale:
        del _auth_failures[ip]


def _record_auth_failure(client_ip: str, now: float) -> None:
    """Record an auth failure for ``client_ip`` and enforce the key cap (C5)."""
    window = _auth_failures.get(client_ip)
    if window is None:
        window = []
        _auth_failures[client_ip] = window
    window.append(now)
    _auth_failures.move_to_end(client_ip)  # mark most-recently-used
    while len(_auth_failures) > _AUTH_FAIL_MAX_KEYS:
        _auth_failures.popitem(last=False)  # evict oldest tracked key


def _require_bearer(
    authorization: str | None,
    cfg: AppConfig,
    *,
    client_ip: str = "unknown",
) -> None:
    """Validate Bearer token with constant-time comparison (S1).

    Raises HTTPException(401/403/429/503) on failure.
    """
    # S10/C5: rate-limit check. Prune expired keys first to keep the map bounded.
    now = time.time()
    _prune_auth_failures(now)
    window = _auth_failures.get(client_ip)
    if window is not None:
        window[:] = [t for t in window if now - t < _AUTH_FAIL_WINDOW]
        recent_failures = len(window)
    else:
        recent_failures = 0
    if recent_failures >= _AUTH_FAIL_LIMIT:
        raise HTTPException(status_code=429, detail="too many auth failures")

    if not authorization or not authorization.startswith("Bearer "):
        _record_auth_failure(client_ip, now)
        raise HTTPException(status_code=401, detail="missing or invalid bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        _record_auth_failure(client_ip, now)
        raise HTTPException(status_code=401, detail="empty bearer token")
    if not cfg.secrets.api_auth_token:
        raise HTTPException(status_code=503, detail="API_AUTH_TOKEN is not configured")
    if not hmac.compare_digest(token, cfg.secrets.api_auth_token):
        _record_auth_failure(client_ip, now)
        raise HTTPException(status_code=403, detail="invalid bearer token")


def _client_ip(request: Request) -> str:
    """Extract the trustworthy client IP for the rate-limit key (C5).

    The app sits behind a single known reverse proxy (Caddy). Caddy *appends*
    the IP of the peer it received the connection from to ``X-Forwarded-For``,
    so the RIGHTMOST entry is the hop the trusted proxy set and is NOT
    client-controllable. The leftmost entries are attacker-supplied and must
    never be used as the limiter key (otherwise a client rotates them to bypass
    the per-IP auth-failure limit). Falls back to the direct peer address.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


def _get_session_factory(cfg: AppConfig) -> async_sessionmaker[AsyncSession]:
    """Return a session factory bound to the shared, loop-aware engine (E1).

    ``_get_engine`` caches one ``AsyncEngine`` per (event loop, url), so every
    request reuses the same pool instead of building and disposing a fresh
    engine per call. The engine lives for the process lifetime and is disposed
    once on app shutdown (see ``app.py`` lifespan) — never inside a handler.
    """
    engine = _get_engine(cfg.secrets.database_url)
    return create_session_factory(engine)


_last_scan_time: float = 0.0


@router.get("/health")
async def health() -> dict[str, str]:
    """C4: PUBLIC minimal liveness probe.

    Returns ONLY ``{"status": "ok" | "degraded"}`` — no Postgres version, Redis
    memory, calendar errors, or per-check details. This endpoint is the only one
    Caddy exposes without a bearer token, so it must not leak any internals.
    Detailed telemetry lives behind auth at ``/health/detail``.
    """
    cfg = AppConfig.load()
    result = await HealthChecker(
        db_url=cfg.secrets.database_url,
        redis_url=cfg.secrets.redis_url,
        litellm_url="",  # library mode — no proxy (D2)
    ).run_all()
    status = "ok" if result.status == HealthStatus.HEALTHY else "degraded"
    return {"status": status}


@router.get("/health/detail")
async def health_detail(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """C4: AUTHENTICATED detailed health + calendar source telemetry (P1-2).

    Exposes per-check details (DB version, Redis memory, calendar last_error,
    etc.). Requires a bearer token so these internals are never public.
    """
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    result = await HealthChecker(
        db_url=cfg.secrets.database_url,
        redis_url=cfg.secrets.redis_url,
        litellm_url="",  # library mode — no proxy (D2)
    ).run_all()
    out = result.to_dict()

    # P1-2: calendar source health telemetry (shared engine — E1).
    try:
        factory = _get_session_factory(cfg)
        async with factory() as session:
            rows = await CalendarSourceHealthRepo(session).all()
            out["calendar_sources"] = [
                {
                    "source": r.source,
                    "last_success": r.last_success.isoformat() if r.last_success else None,
                    "last_error": r.last_error,
                    "consecutive_failures": r.consecutive_failures,
                    "last_attempt": r.last_attempt.isoformat() if r.last_attempt else None,
                }
                for r in rows
            ]
    except Exception:
        out["calendar_sources"] = []

    return out


@router.get("/signals")
async def list_signals(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """List recent signals."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    async with factory() as session:
        signals = await SignalRepo(session).recent(limit)
        return {
            "signals": [_serialize_signal(s, include_payload=False) for s in signals],
            "total": len(signals),
            "limit": limit,
        }


@router.get("/signals/{signal_id}")
async def get_signal(
    signal_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Get signal detail including payload."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    async with factory() as session:
        signal = await SignalRepo(session).get(signal_id)
        if signal is None:
            raise HTTPException(status_code=404, detail=f"signal {signal_id} not found")
        return _serialize_signal(signal, include_payload=True)


@router.get("/calibration")
async def calibration(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Calibration metrics over the last 30 days."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    since = datetime.now(UTC) - timedelta(days=30)
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


@router.post("/scan")
async def trigger_scan(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    symbol: Annotated[str, Query(min_length=3)] = "XAUUSD",
    timeframe: Annotated[Timeframe, Query()] = Timeframe.H1,
) -> dict[str, object]:
    """Run a manual scan immediately."""
    global _last_scan_time

    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))

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
async def prometheus_metrics(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    """Return simple signal counters."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    async with factory() as session:
        result = await session.execute(select(Signal.status))
        statuses = [str(s) for s in result.scalars().all()]
        return {
            "signals_published": statuses.count(SignalStatus.PUBLISHED.value),
            "signals_rejected": statuses.count(SignalStatus.REJECTED.value),
            "signals_abstained": statuses.count(SignalStatus.ABSTAINED.value),
        }


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
async def analytics_exits(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Virtual exit policy comparison (W10)."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    async with factory() as session:
        result = await session.execute(select(Signal).where(Signal.outcome_r.is_not(None)))
        signals = list(result.scalars().all())
        payloads = [s.payload or {} for s in signals]
        return _aggregate_exits(payloads)


@router.get("/analytics/excursion")
async def analytics_excursion(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """MAE/MFE excursion analytics (W10)."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
    async with factory() as session:
        result = await session.execute(select(Signal).where(Signal.outcome_r.is_not(None)))
        signals = list(result.scalars().all())
        payloads = [
            {**(s.payload or {}), "outcome_r": float(s.outcome_r)}
            for s in signals
            if s.outcome_r is not None
        ]
        return _aggregate_excursion(payloads)


@router.get("/analytics/failures")
async def analytics_failures(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Coroner failure mode distribution (W10)."""
    cfg = AppConfig.load()
    _require_bearer(authorization, cfg, client_ip=_client_ip(request))
    factory = _get_session_factory(cfg)
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
