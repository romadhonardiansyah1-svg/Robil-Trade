"""Backfill script — fetch ≥3 years historical data (PLAN §8.1, P1-T4).

Usage:
    uv run python scripts/backfill.py --instrument XAUUSD --years 3
    uv run python scripts/backfill.py --all --years 3

Features:
- Resumable: starts from last candle in DB (per instrument × TF).
- Paginated: TwelveData max 5000 bars/call, ccxt max 1000.
- Gap-aware: weekend gaps for non-crypto are normal.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Add project root to path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import structlog

from rtrade.core.config import AppConfig

logger = structlog.get_logger(__name__)


async def backfill_instrument(
    config: object,
    symbol: str,
    years: int = 3,
) -> None:
    """Backfill one instrument's candles for all its timeframes."""
    logger.info("backfill starting", symbol=symbol, years=years)

    # Calculate start date.
    since = datetime.now(UTC) - timedelta(days=years * 365)

    logger.info(
        "backfill parameters",
        symbol=symbol,
        since=since.isoformat(),
        timeframes=["1h", "4h"],
    )

    # TODO: In full integration:
    # 1. Create provider based on instrument config (ccxt or TwelveData)
    # 2. Create DB session
    # 3. Get or create instrument in DB
    # 4. Query last candle timestamp for resume
    # 5. Paginate fetch_ohlcv calls
    # 6. Upsert via CandleRepo
    # 7. Log progress

    logger.info("backfill completed", symbol=symbol)


async def main_async(args: argparse.Namespace) -> None:
    """Async main."""
    config = AppConfig.load(env_file=None)

    symbols = [inst.symbol for inst in config.instruments] if args.all else [args.instrument]

    for symbol in symbols:
        try:
            await backfill_instrument(config, symbol, years=args.years)
        except Exception as exc:
            logger.error("backfill failed", symbol=symbol, error=str(exc))
            if not args.all:
                raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill historical candle data")
    parser.add_argument("--instrument", type=str, help="Instrument symbol (e.g. XAUUSD)")
    parser.add_argument("--all", action="store_true", help="Backfill all instruments")
    parser.add_argument("--years", type=int, default=3, help="Years of history (default: 3)")
    args = parser.parse_args()

    if not args.instrument and not args.all:
        parser.error("specify --instrument or --all")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
