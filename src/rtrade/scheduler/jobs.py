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

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import structlog

from rtrade.core.config import AppConfig
from rtrade.core.errors import RateLimitExceeded
from rtrade.monitoring.healthcheck import HealthChecker
from rtrade.pipeline import run_scan, sync_calendar, track_paper_signals

logger = structlog.get_logger(__name__)

# F3: consecutive failure tracking for alert.
_fail_counts: dict[str, int] = {}
_ALERT_THRESHOLD = 3
# F3: once-then-cooldown alert state — last alert timestamp per key.
_last_alert_at: dict[str, datetime] = {}
_ALERT_COOLDOWN = timedelta(hours=2)


async def scan_job(
    symbol: str,
    timeframe: str,
) -> None:
    """Execute one scan cycle for an instrument × timeframe.

    Order (PLAN §8.12):
    ingest → indicators → regime → strategies → confluence → levels →
    risk → guardrails → output → papertrack
    """
    key = f"{symbol}:{timeframe}"
    logger.info("scan_job started", symbol=symbol, timeframe=timeframe)
    try:
        result = await run_scan(symbol, timeframe)
        _fail_counts[key] = 0
        logger.info(
            "scan_job completed",
            symbol=symbol,
            timeframe=timeframe,
            status=result.status,
            signal_id=result.signal_id,
            failures=result.failures,
        )
    except Exception as exc:
        _fail_counts[key] = _fail_counts.get(key, 0) + 1
        logger.error(
            "scan_job failed",
            symbol=symbol,
            timeframe=timeframe,
            error=str(exc),
            consecutive_failures=_fail_counts[key],
        )
        # Rate-limit bursts must never spam Telegram — count but never alert (F3).
        if isinstance(exc, RateLimitExceeded):
            return
        if _fail_counts[key] >= _ALERT_THRESHOLD:
            now = datetime.now(UTC)
            last = _last_alert_at.get(key)
            if last is None or now - last >= _ALERT_COOLDOWN:
                await _send_failure_alert(
                    f"⚠️ scan {key} gagal {_fail_counts[key]}x berturut-turut: {exc}"
                )
                _last_alert_at[key] = now


async def _send_failure_alert(message: str) -> None:
    """Best-effort alert to Telegram on repeated failures (F3)."""
    try:
        cfg = AppConfig.load()
        if cfg.secrets.telegram_bot_token and cfg.secrets.telegram_chat_id:
            from rtrade.delivery.telegram_bot import TelegramDelivery

            tg = TelegramDelivery(cfg.secrets.telegram_bot_token, cfg.secrets.telegram_chat_id)
            try:
                await tg.send_alert(message)
            finally:
                await tg.close()
    except Exception:
        logger.exception("failed to send failure alert")


async def _run_job(name: str, coro_fn: Callable[[], Awaitable[None]]) -> None:
    """Standardized wrapper for non-scan jobs: log start/end, catch and log
    exceptions, and emit a best-effort failure alert so a crashing job never
    silently dies inside APScheduler.
    """
    logger.info("job started", job=name)
    try:
        await coro_fn()
        logger.info("job completed", job=name)
    except Exception as exc:
        logger.exception("job failed", job=name)
        # Best-effort alert; an alert failure must never mask the original error.
        try:
            await _send_failure_alert(f"⚠️ job {name} gagal: {exc}")
        except Exception:
            logger.exception("failed to send job failure alert", job=name)


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
        litellm_url="",  # library mode — no proxy (D2)
    ).run_all()
    logger.info("health check completed", status=health.status.value)


async def hmm_train_job() -> None:
    """Weekly HMM retrain per instrument (Sunday 02:00 UTC) (W8)."""
    import pandas as pd

    from rtrade.core.constants import Timeframe
    from rtrade.indicators.engine import compute as compute_indicators
    from rtrade.persistence.db import create_engine, create_session_factory
    from rtrade.persistence.repositories import CandleRepo, InstrumentRepo
    from rtrade.regime.hmm import HMMRegimeDetector

    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            for inst in cfg.instruments:
                row = await InstrumentRepo(session).get_by_symbol(inst.symbol)
                if row is None:
                    continue
                candles = await CandleRepo(session).latest_n(row.id, Timeframe.H1, 5000)
                if len(candles) < 600:
                    continue
                df = pd.DataFrame(
                    [
                        {
                            "ts": c.ts,
                            "open": float(c.open),
                            "high": float(c.high),
                            "low": float(c.low),
                            "close": float(c.close),
                            "volume": float(c.volume),
                        }
                        for c in candles
                    ]
                )
                df = compute_indicators(df)
                detector = HMMRegimeDetector()
                detector.train(df)
                from pathlib import Path

                from rtrade.ml.model_io import save_model

                out = Path("models")
                out.mkdir(exist_ok=True)
                save_model(detector, out / f"hmm_{inst.symbol}.joblib")
                logger.info("hmm trained", symbol=inst.symbol)
    finally:
        await engine.dispose()


async def audit_chain_verify_job() -> None:
    """Periodic audit-chain integrity check (P2-8, S9).

    Samples last 1000 audit rows, verifies hash chain. Alert on break.
    """
    from sqlalchemy import select

    from rtrade.persistence.audit_chain import verify_chain
    from rtrade.persistence.db import create_engine, create_session_factory
    from rtrade.persistence.models import SignalAudit

    logger.info("audit chain verify started")
    cfg = AppConfig.load()
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            result = await session.execute(
                select(SignalAudit).order_by(SignalAudit.id.asc()).limit(1000)
            )
            rows = result.scalars().all()
            entries = [
                {
                    "stage": r.stage,
                    "ok": r.ok,
                    "signal_id": r.signal_id,
                    "detail": r.detail,
                }
                for r in rows
            ]
        ok, count_or_idx = verify_chain(entries)
        if ok:
            logger.info("audit chain verify PASSED", rows_checked=count_or_idx)
        else:
            logger.critical(
                "audit chain BROKEN — tampering or corruption detected",
                broken_at_index=count_or_idx,
            )
            await _send_failure_alert(
                f"🚨 AUDIT CHAIN BROKEN at index {count_or_idx} — possible tampering!"
            )
    except Exception as exc:
        logger.error("audit chain verify failed", error=str(exc))
    finally:
        await engine.dispose()


# A5: named async wrappers so non-scan jobs run through the standardized
# _run_job error/alert handling. Registered in scheduler.main.create_scheduler().
# (scan_job is intentionally NOT wrapped — it owns its _fail_counts/cooldown logic.)


async def calendar_sync_job_wrapped() -> None:
    await _run_job("calendar_sync", calendar_sync_job)


async def paper_track_job_wrapped() -> None:
    await _run_job("paper_track", paper_track_job)


async def health_check_job_wrapped() -> None:
    await _run_job("health_check", health_check_job)


async def hmm_train_job_wrapped() -> None:
    await _run_job("hmm_train", hmm_train_job)


async def audit_chain_verify_job_wrapped() -> None:
    await _run_job("audit_chain_verify", audit_chain_verify_job)
