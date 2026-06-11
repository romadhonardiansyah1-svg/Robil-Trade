"""Scheduler entrypoint -- APScheduler worker process (PLAN 8.12).

Starts the APScheduler with cron triggers for each instrument × TF
at candle_close + 30 seconds. Also schedules calendar sync, paper-tracking,
and health checks.
"""

from __future__ import annotations

import asyncio
import signal

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from rtrade.scheduler.jobs import (
    calendar_sync_job,
    health_check_job,
    paper_track_job,
    scan_job,
)

logger = structlog.get_logger(__name__)

# Instrument x TF scan schedule: candle_close + 30s.
# 1H: minute=0, second=30 every hour.
# 4H: minute=0, second=30 at 00/04/08/12/16/20 UTC.
_SCAN_SCHEDULES = [
    # (symbol, timeframe, cron_kwargs)
    ("XAUUSD", "1h", {"minute": "0", "second": "30"}),
    ("XAUUSD", "4h", {"minute": "0", "second": "30", "hour": "0,4,8,12,16,20"}),
    ("EURUSD", "1h", {"minute": "0", "second": "30"}),
    ("EURUSD", "4h", {"minute": "0", "second": "30", "hour": "0,4,8,12,16,20"}),
    ("BTCUSDT", "1h", {"minute": "0", "second": "30"}),
    ("BTCUSDT", "4h", {"minute": "0", "second": "30", "hour": "0,4,8,12,16,20"}),
]


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    # Scan jobs (instrument x timeframe).
    for symbol, tf, cron_kw in _SCAN_SCHEDULES:
        scheduler.add_job(
            scan_job,
            trigger=CronTrigger(**cron_kw, timezone="UTC"),
            kwargs={"symbol": symbol, "timeframe": tf},
            id=f"scan_{symbol}_{tf}",
            name=f"Scan {symbol} {tf}",
            replace_existing=True,
            misfire_grace_time=120,
        )

    # Calendar sync: 2×/day at 00:15 and 12:15 UTC.
    scheduler.add_job(
        calendar_sync_job,
        trigger=CronTrigger(hour="0,12", minute="15", timezone="UTC"),
        id="calendar_sync",
        name="Calendar sync",
        replace_existing=True,
    )

    # Paper tracker: every 15 minutes.
    scheduler.add_job(
        paper_track_job,
        trigger=IntervalTrigger(minutes=15),
        id="paper_track",
        name="Paper tracker",
        replace_existing=True,
    )

    # Health check: every 5 minutes.
    scheduler.add_job(
        health_check_job,
        trigger=IntervalTrigger(minutes=5),
        id="health_check",
        name="Health check",
        replace_existing=True,
    )

    return scheduler


async def run_worker() -> None:
    """Main worker entrypoint."""
    logger.info("starting Robil Trade worker")

    scheduler = create_scheduler()
    scheduler.start()

    logger.info(
        "scheduler started",
        jobs=[j.id for j in scheduler.get_jobs()],
    )

    # Keep running until interrupted.
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler.
            pass

    await stop_event.wait()
    scheduler.shutdown()
    logger.info("worker shut down")


def main() -> None:
    """CLI entrypoint for the worker."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
