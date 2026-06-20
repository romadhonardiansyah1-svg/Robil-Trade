"""Scheduler entrypoint -- APScheduler worker process (PLAN 8.12).

Starts the APScheduler with cron triggers for each instrument × TF
at candle_close + 30 seconds. Also schedules calendar sync, paper-tracking,
and health checks.
"""

from __future__ import annotations

import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
import structlog

from rtrade.core.config import AppConfig, InstrumentConfig
from rtrade.core.constants import Timeframe
from rtrade.monitoring.alerts import AlertManager
from rtrade.scheduler import jobs
from rtrade.scheduler.jobs import (
    audit_chain_verify_job_wrapped,
    calendar_sync_job_wrapped,
    health_check_job_wrapped,
    hmm_train_job_wrapped,
    paper_track_job_wrapped,
    scan_job,
)

logger = structlog.get_logger(__name__)


def _has_calendar_source(cfg: AppConfig) -> bool:
    """True bila setidaknya satu source kalender aktif dan buildable."""
    for src in cfg.settings.calendar.sources:
        if not src.enabled:
            continue
        if src.name == "finnhub":
            if cfg.secrets.finnhub_api_key:
                return True
            continue  # finnhub without key → skip
        if src.name in {"investing", "static_high_impact", "nasdaq", "trading_economics"}:
            return True
    return False


# BUG 3: spread TwelveData H1 instruments across the hour to avoid a credit burst.
_TWELVEDATA_H1_MINUTES = ["0", "10", "20", "30"]


def build_scan_schedules(
    instruments: list[InstrumentConfig],
) -> list[tuple[str, str, dict[str, str]]]:
    """One (symbol, tf, cron_kwargs) per instrument×TF; stagger to avoid bursts.

    TwelveData shares one rate-limited free bucket, so its H1 scans are spread
    across minutes ``["0","10","20","30"]`` (all ``second="30"``) by per-TwelveData
    instrument index, and H4 is moved onto ``minute="5"`` to clear the H1 boundary
    (BUG 3). Non-TwelveData instruments keep the original second-stagger.
    """
    schedules: list[tuple[str, str, dict[str, str]]] = []
    td_idx = 0  # per-TwelveData-instrument index for the minute spread
    for idx, inst in enumerate(instruments):
        is_twelvedata = inst.provider == "twelvedata"
        second = str(30 + (idx * 5) % 30)  # 30,35,40,... avoiding burst
        h1_minute = "0"
        if is_twelvedata:
            h1_minute = _TWELVEDATA_H1_MINUTES[td_idx % len(_TWELVEDATA_H1_MINUTES)]
            td_idx += 1
        for tf in inst.timeframes:
            if tf == Timeframe.H1:
                if is_twelvedata:
                    cron = {"minute": h1_minute, "second": "30"}
                else:
                    cron = {"minute": "0", "second": second}
            elif tf == Timeframe.H4:
                if is_twelvedata:
                    cron = {"minute": "5", "second": "30", "hour": "0,4,8,12,16,20"}
                else:
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
        calendar_sync_job_wrapped,
        trigger=CronTrigger(hour="0,12", minute="15", timezone="UTC"),
        id="calendar_sync",
        name="Calendar sync",
        replace_existing=True,
    )

    # Paper tracker: every 15 minutes.
    scheduler.add_job(
        paper_track_job_wrapped,
        trigger=IntervalTrigger(minutes=15),
        id="paper_track",
        name="Paper tracker",
        replace_existing=True,
    )

    # Health check: every 5 minutes.
    scheduler.add_job(
        health_check_job_wrapped,
        trigger=IntervalTrigger(minutes=5),
        id="health_check",
        name="Health check",
        replace_existing=True,
    )

    # W8: HMM retrain: weekly Sunday 02:00 UTC.
    scheduler.add_job(
        hmm_train_job_wrapped,
        trigger=CronTrigger(day_of_week="sun", hour="2", minute="0", timezone="UTC"),
        id="hmm_train",
        name="HMM weekly retrain",
        replace_existing=True,
    )

    # P2-8: Audit chain integrity verification: weekly Sunday 03:00 UTC.
    scheduler.add_job(
        audit_chain_verify_job_wrapped,
        trigger=CronTrigger(day_of_week="sun", hour="3", minute="0", timezone="UTC"),
        id="audit_chain_verify",
        name="Audit chain verify",
        replace_existing=True,
    )

    return scheduler


async def run_worker() -> None:
    """Main worker entrypoint."""
    from rtrade.core.logging_setup import configure_logging

    configure_logging()

    # S11: guardrail integrity self-test — fail-closed if broken
    from rtrade.guardrails.selftest import run_guardrail_selftest

    problems = run_guardrail_selftest()
    if problems:
        logger.critical("guardrail selftest FAILED — refusing to start", problems=problems)
        raise SystemExit(1)
    logger.info("guardrail selftest passed")

    # FR-SCH-07: startup warning if no calendar source available.
    cfg = AppConfig.load()
    has_non_crypto = any(i.market.value != "crypto" for i in cfg.instruments)
    if has_non_crypto and not _has_calendar_source(cfg):
        if cfg.settings.calendar.fail_open_when_stale:
            logger.warning(
                "no calendar source; fail-open active — non-crypto akan trade buta terhadap berita"
            )
        else:
            logger.critical(
                "no calendar source; GR-07b akan REJECT SEMUA signal non-crypto — refusing to start"
            )
            raise SystemExit(1)

    logger.info("starting Robil Trade worker")

    # P2-5 (A6): wire the process-scoped AlertManager so the live worker routes
    # typed failure alerts through per-type cooldown dedup. Enabled only when both
    # Telegram credentials are present; otherwise stay disabled (direct fallback).
    if cfg.secrets.telegram_bot_token and cfg.secrets.telegram_chat_id:
        jobs._alert_manager = AlertManager(
            cfg.secrets.telegram_bot_token,
            cfg.secrets.telegram_chat_id,
            enabled=True,
        )
        logger.info("alert manager enabled (dedup path active)")
    else:
        logger.warning("alert manager disabled — telegram credentials missing")

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
