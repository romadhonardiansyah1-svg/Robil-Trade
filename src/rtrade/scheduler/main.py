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

from rtrade.core.config import AppConfig, InstrumentConfig
from rtrade.core.constants import Timeframe
from rtrade.scheduler.jobs import (
    calendar_sync_job,
    health_check_job,
    paper_track_job,
    scan_job,
)

logger = structlog.get_logger(__name__)


def build_scan_schedules(
    instruments: list[InstrumentConfig],
) -> list[tuple[str, str, dict[str, str]]]:
    """One (symbol, tf, cron_kwargs) per instrument×TF; stagger seconds to avoid bursts."""
    schedules: list[tuple[str, str, dict[str, str]]] = []
    for idx, inst in enumerate(instruments):
        second = str(30 + (idx * 5) % 30)  # 30,35,40,... avoiding burst
        for tf in inst.timeframes:
            if tf == Timeframe.H1:
                cron = {"minute": "0", "second": second}
            elif tf == Timeframe.H4:
                cron = {"minute": "0", "second": second, "hour": "0,4,8,12,16,20"}
            else:  # D1
                cron = {"minute": "1", "second": second, "hour": "0"}
            schedules.append((inst.symbol, tf.value, cron))
    return schedules


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the scheduler."""
    scheduler = AsyncIOScheduler(timezone="UTC")

    instruments = AppConfig.load().instruments
    scan_schedules = build_scan_schedules(instruments)

    # Scan jobs (instrument x timeframe).
    for symbol, tf, cron_kw in scan_schedules:
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
