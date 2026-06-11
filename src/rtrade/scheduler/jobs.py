"""Job definitions for the scheduler (PLAN §8.12).

Jobs:
- Scan job: per (instrument × TF) at candle_close + 30s buffer.
- Calendar sync: 2×/day (00:15 and 12:15 UTC).
- Paper-tracker: every 15 minutes.
- Health check: every 5 minutes.

All jobs are idempotent. Duplicate scan of the same bar does not produce
duplicate signals (dedup key: instrument, timeframe, strategy, bar_ts).
"""

from __future__ import annotations

import structlog

from rtrade.core.config import AppConfig
from rtrade.monitoring.healthcheck import HealthChecker
from rtrade.pipeline import run_scan, sync_calendar, track_paper_signals

logger = structlog.get_logger(__name__)


async def scan_job(
    symbol: str,
    timeframe: str,
) -> None:
    """Execute one scan cycle for an instrument × timeframe.

    Order (PLAN §8.12):
    ingest → indicators → regime → strategies → confluence → levels →
    risk → guardrails → output → papertrack
    """
    logger.info("scan_job started", symbol=symbol, timeframe=timeframe)
    result = await run_scan(symbol, timeframe)
    logger.info(
        "scan_job completed",
        symbol=symbol,
        timeframe=timeframe,
        status=result.status,
        signal_id=result.signal_id,
        failures=result.failures,
    )


async def calendar_sync_job() -> None:
    """Sync economic calendar from Finnhub (2×/day)."""
    logger.info("calendar sync started")
    count = await sync_calendar()
    logger.info("calendar sync completed", events=count)


async def paper_track_job() -> None:
    """Check fills/SL/TP/expiry for all PUBLISHED/FILLED signals (every 15 min)."""
    logger.info("paper tracking started")
    updates = await track_paper_signals()
    logger.info("paper tracking completed", updates=updates)


async def health_check_job() -> None:
    """Check provider availability (every 5 min)."""
    logger.info("health check started")
    cfg = AppConfig.load()
    health = await HealthChecker(
        db_url=cfg.secrets.database_url,
        redis_url=cfg.secrets.redis_url,
        litellm_url=cfg.secrets.litellm_base_url,
    ).run_all()
    logger.info("health check completed", status=health.status.value)
