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
    # TODO: Wire full pipeline in P1 integration.
    # This is the orchestration point that ties all modules together.
    logger.info("scan_job completed", symbol=symbol, timeframe=timeframe)


async def calendar_sync_job() -> None:
    """Sync economic calendar from Finnhub (2×/day)."""
    logger.info("calendar sync started")
    # TODO: Fetch events from FinnhubCalendarProvider and upsert to DB.
    logger.info("calendar sync completed")


async def paper_track_job() -> None:
    """Check fills/SL/TP/expiry for all PUBLISHED/FILLED signals (every 15 min)."""
    logger.info("paper tracking started")
    # TODO: Query PUBLISHED signals, check fills against latest candles.
    logger.info("paper tracking completed")


async def health_check_job() -> None:
    """Check provider availability (every 5 min)."""
    logger.info("health check started")
    # TODO: Ping providers, DB, Redis. Alert on failure.
    logger.info("health check completed")
