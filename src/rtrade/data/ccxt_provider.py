"""ccxt-based data provider for crypto (Binance public).

Implements MarketDataProvider + DerivativesProvider for BTC/USDT (and future
crypto instruments). Uses ccxt async for OHLCV and httpx for Binance Futures
public endpoints (funding rate, open interest) — no API key needed (PLAN §8.1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from typing import Any

import ccxt.async_support as ccxt_async
import httpx
import structlog

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError, ProviderError
from rtrade.core.timeutil import ensure_utc
from rtrade.data.base import (
    Candle,
    DerivativesProvider,
    FundingSnapshot,
    MarketDataProvider,
    OISnapshot,
    Quote,
)
from rtrade.data.ratelimit import BINANCE_PUBLIC_BUCKET, CCXT_BINANCE_BUCKET, RateLimiter

logger = structlog.get_logger(__name__)

_TF_MAP: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1d",
}

_BINANCE_FAPI = "https://fapi.binance.com"


class CcxtProvider(MarketDataProvider, DerivativesProvider):
    """Binance public OHLCV + derivatives via ccxt + httpx (PLAN §8.1, ADR-03).

    No API key required — all endpoints are public/read-only.
    """

    def __init__(
        self,
        rate_limiter: RateLimiter,
        *,
        http_timeout: float = 15.0,
    ) -> None:
        self._limiter = rate_limiter
        self._exchange = ccxt_async.binance(
            {
                "enableRateLimit": False,  # we handle rate limiting ourselves
                "options": {"defaultType": "spot"},
            }
        )
        self._http = httpx.AsyncClient(
            base_url=_BINANCE_FAPI,
            timeout=http_timeout,
            headers={"User-Agent": "RobilTrade/0.1"},
        )

    # -- MarketDataProvider --------------------------------------------------

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch OHLCV candles from Binance spot via ccxt."""
        since_utc = ensure_utc(since)
        since_ms = int(since_utc.timestamp() * 1000)
        tf_str = _TF_MAP.get(timeframe)
        if tf_str is None:
            raise ProviderError(f"unsupported timeframe for ccxt: {timeframe}")

        await self._limiter.acquire(CCXT_BINANCE_BUCKET)

        try:
            raw: list[list[Any]] = await self._exchange.fetch_ohlcv(
                symbol, tf_str, since=since_ms, limit=limit
            )
        except ccxt_async.BaseError as exc:
            raise ProviderError(f"ccxt fetch_ohlcv failed for {symbol}: {exc}") from exc

        candles: list[Candle] = []
        for row in raw:
            ts_ms, o, h, l, c, v = row[:6]
            ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
            try:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=ts,
                        open=Decimal(str(o)),
                        high=Decimal(str(h)),
                        low=Decimal(str(l)),
                        close=Decimal(str(c)),
                        volume=Decimal(str(v)),
                    )
                )
            except (ValueError, DataValidationError) as exc:
                logger.warning("skipping invalid candle", ts=ts, error=str(exc))

        logger.info(
            "ccxt ohlcv fetched",
            symbol=symbol,
            timeframe=timeframe.value,
            count=len(candles),
            since=since_utc.isoformat(),
        )
        return candles

    async def fetch_quote(self, symbol: str) -> Quote:
        """Fetch latest ticker price from Binance."""
        await self._limiter.acquire(CCXT_BINANCE_BUCKET)
        try:
            ticker: dict[str, Any] = await self._exchange.fetch_ticker(symbol)
        except ccxt_async.BaseError as exc:
            raise ProviderError(f"ccxt fetch_ticker failed for {symbol}: {exc}") from exc

        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise ProviderError(f"no price in ticker for {symbol}")

        return Quote(
            symbol=symbol,
            price=Decimal(str(price)),
            ts=datetime.now(UTC),
        )

    # -- DerivativesProvider -------------------------------------------------

    async def fetch_funding_rate(self, symbol: str) -> FundingSnapshot:
        """Fetch latest funding rate from Binance Futures public API."""
        # Convert ccxt symbol to Binance format: "BTC/USDT" → "BTCUSDT"
        binance_symbol = symbol.replace("/", "")
        await self._limiter.acquire(BINANCE_PUBLIC_BUCKET)

        try:
            resp = await self._http.get(
                "/fapi/v1/fundingRate",
                params={"symbol": binance_symbol, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Binance funding rate HTTP {exc.response.status_code}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Binance funding rate failed: {exc}") from exc

        if not data:
            raise ProviderError(f"no funding rate data for {symbol}")

        latest = data[0]
        return FundingSnapshot(
            symbol=symbol,
            funding_rate=Decimal(str(latest["fundingRate"])),
            ts=datetime.fromtimestamp(latest["fundingTime"] / 1000, tz=UTC),
        )

    async def fetch_open_interest(self, symbol: str) -> OISnapshot:
        """Fetch latest open interest from Binance Futures public API."""
        binance_symbol = symbol.replace("/", "")
        await self._limiter.acquire(BINANCE_PUBLIC_BUCKET)

        try:
            resp = await self._http.get(
                "/futures/data/openInterestHist",
                params={"symbol": binance_symbol, "period": "1h", "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Binance OI HTTP {exc.response.status_code}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Binance OI failed: {exc}") from exc

        if not data:
            raise ProviderError(f"no open interest data for {symbol}")

        latest = data[0]
        return OISnapshot(
            symbol=symbol,
            open_interest=Decimal(str(latest["sumOpenInterest"])),
            ts=datetime.fromtimestamp(latest["timestamp"] / 1000, tz=UTC),
        )

    # -- T21: Spread -----------------------------------------------------------

    async def fetch_spread(self, symbol: str) -> float | None:
        """Return bid/ask spread from Binance ticker (T21)."""
        await self._limiter.acquire(CCXT_BINANCE_BUCKET)
        try:
            ticker: dict[str, Any] = await self._exchange.fetch_ticker(symbol)
        except ccxt_async.BaseError as exc:
            logger.warning("fetch_spread failed", error=str(exc))
            return None
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None:
            return None
        return float(ask) - float(bid)

    # -- lifecycle -----------------------------------------------------------

    async def close(self) -> None:
        await self._exchange.close()
        await self._http.aclose()


def _event_id(provider: str, event: str, event_time: datetime) -> str:
    """Deterministic ID for deduplication."""
    raw = f"{provider}:{event}:{event_time.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
