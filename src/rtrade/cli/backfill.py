"""CLI: backfill candle data for one instrument × timeframe (T14).

Usage:
    uv run python -m rtrade.cli.backfill XAUUSD 1h --days 365
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import structlog

from rtrade.core.config import AppConfig, InstrumentConfig
from rtrade.core.constants import Timeframe
from rtrade.core.errors import ConfigError
from rtrade.core.timeutil import timeframe_duration
from rtrade.data.base import MarketDataProvider
from rtrade.data.ingestion import ingest_candles
from rtrade.data.ratelimit import RateLimiter
from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.repositories import CandleRepo, InstrumentRepo

logger = structlog.get_logger(__name__)

# TwelveData/CCXT page size used per fetch. The cursor advances by exactly this
# many candles each batch so no window is re-fetched (waste) or skipped (gaps).
_BATCH_SIZE = 499


def _advance_cursor(since: datetime, tf: Timeframe, batch: int = _BATCH_SIZE) -> datetime:
    """Advance the pagination cursor by `batch` candles of timeframe `tf`.

    Uses `timeframe_duration` so every timeframe (M5…D1) steps by the correct
    amount: D1 advances ~`batch` days, M5 advances ~`batch`×5min, etc.
    """
    return since + timeframe_duration(tf) * batch


def _make_provider(
    instrument: InstrumentConfig, cfg: AppConfig, limiter: RateLimiter
) -> MarketDataProvider:
    from rtrade.data.ccxt_provider import CcxtProvider
    from rtrade.data.twelvedata_provider import TwelveDataProvider

    if instrument.provider == "twelvedata":
        return TwelveDataProvider(cfg.secrets.twelvedata_api_key, limiter)
    if instrument.provider == "ccxt_binance":
        return CcxtProvider(limiter)
    raise ConfigError(f"unsupported provider: {instrument.provider}")


async def _run(symbol: str, timeframe: str, days: int, config_dir: str) -> None:
    cfg = AppConfig.load(config_dir=Path(config_dir))
    tf = Timeframe(timeframe)
    instrument = cfg.instrument(symbol)

    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(cfg.secrets.redis_url)
    limiter = RateLimiter(redis_client)
    provider = _make_provider(instrument, cfg, limiter)
    engine = create_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    since = datetime.now(UTC) - timedelta(days=days)
    logger.info(
        "backfill started",
        symbol=symbol,
        timeframe=timeframe,
        since=since.isoformat(),
    )

    try:
        async with session_factory() as session:
            inst_repo = InstrumentRepo(session)
            inst_row = await inst_repo.get_or_create(
                symbol=instrument.symbol,
                market=instrument.market.value,
                provider=instrument.provider,
                provider_symbol=instrument.provider_symbol,
                pip_size=Decimal(str(instrument.pip_size)),
                config=instrument.model_dump(mode="json"),
            )
            candle_repo = CandleRepo(session)

            # Pagination: fetch in batches of 500, advance `since` each time.
            total = 0
            batch_since = since
            batch_num = 0
            while batch_since < datetime.now(UTC):
                batch_num += 1
                count = await ingest_candles(
                    provider, instrument, inst_row.id, tf, candle_repo, since=batch_since
                )
                await session.commit()
                total += count
                if count == 0:
                    break
                # Advance the cursor by one full page of candles for this
                # timeframe (tf-aware: no overlap on D1, no gaps on M5/M15).
                batch_since = _advance_cursor(batch_since, tf)
                logger.info(
                    "backfill batch done",
                    batch=batch_num,
                    count=count,
                    total=total,
                    next_since=batch_since.isoformat(),
                )
                # Pause to respect rate limits (free tier: 8 req/min)
                await asyncio.sleep(15)

            logger.info("backfill completed", symbol=symbol, timeframe=timeframe, total=total)
    finally:
        await provider.close()
        await redis_client.aclose()
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill candle data")
    parser.add_argument("symbol", help="e.g. XAUUSD")
    parser.add_argument("timeframe", help="e.g. 1h, 4h, 1d")
    parser.add_argument("--days", type=int, default=365, help="backfill depth in days")
    parser.add_argument("--config-dir", default="config", help="config directory")
    args = parser.parse_args()

    asyncio.run(_run(args.symbol, args.timeframe, args.days, args.config_dir))


if __name__ == "__main__":
    main()
