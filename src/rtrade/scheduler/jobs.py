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

# F3: consecutive failure tracking for alert.
_fail_counts: dict[str, int] = {}
_ALERT_THRESHOLD = 3


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
        if _fail_counts[key] >= _ALERT_THRESHOLD:
            await _send_failure_alert(
                f"⚠️ scan {key} gagal {_fail_counts[key]}x berturut-turut: {exc}"
            )


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
