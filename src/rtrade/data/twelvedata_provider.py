"""TwelveData provider for XAUUSD and Forex OHLCV data (PLAN §8.1, ADR-03).

Free tier limits: 8 req/min, 800 req/day, max 5000 data points per call.
Rate limiting is handled by the shared RateLimiter (token bucket 7/min).
Pagination for backfill uses start_date/end_date parameters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rtrade.core.constants import Timeframe
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.core.timeutil import ensure_utc
from rtrade.data.base import Candle, MarketDataProvider, Quote
from rtrade.data.ratelimit import TWELVEDATA_BUCKET, RateLimiter

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.twelvedata.com"

_TF_MAP: dict[Timeframe, str] = {
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1day",
}


class TwelveDataProvider(MarketDataProvider):
    """XAUUSD and Forex data via TwelveData REST API (PLAN §8.1).

    Requires a free-tier API key (TWELVEDATA_API_KEY env var).
    """

    def __init__(
        self,
        api_key: str,
        rate_limiter: RateLimiter,
        *,
        http_timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise ProviderError("TWELVEDATA_API_KEY is required")
        self._api_key = api_key
        self._limiter = rate_limiter
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=http_timeout,
            headers={"User-Agent": "RobilTrade/0.1"},
        )

    @retry(
        retry=retry_if_exception_type(RateLimitExceeded),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        since: datetime,
        limit: int = 500,
    ) -> list[Candle]:
        """Fetch historical OHLCV from TwelveData.

        TwelveData returns data newest-first; we reverse to ascending order.
        Max 5000 points per call. For backfill, caller pages via `since`.
        """
        since_utc = ensure_utc(since)
        tf_str = _TF_MAP.get(timeframe)
        if tf_str is None:
            raise ProviderError(f"unsupported timeframe for TwelveData: {timeframe}")

        await self._limiter.acquire(TWELVEDATA_BUCKET)

        params: dict[str, str | int] = {
            "symbol": symbol,
            "interval": tf_str,
            "start_date": since_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "outputsize": min(limit, 5000),
            "apikey": self._api_key,
            "format": "JSON",
            "timezone": "UTC",
        }

        try:
            resp = await self._http.get("/time_series", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"TwelveData HTTP error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("TwelveData 429: rate limit hit")
        if resp.status_code >= 400:
            raise ProviderError(f"TwelveData HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()

        # TwelveData error codes come in the JSON body.
        if body.get("status") == "error":
            code = body.get("code", 0)
            msg = body.get("message", "unknown error")
            if code == 429:
                raise RateLimitExceeded(f"TwelveData API rate limit: {msg}")
            raise ProviderError(f"TwelveData API error {code}: {msg}")

        values = body.get("values", [])
        if not values:
            logger.warning("TwelveData returned no data", symbol=symbol)
            return []

        candles: list[Candle] = []
        for row in values:
            try:
                raw_dt = row["datetime"]
                if len(raw_dt) <= 10:
                    # D1 candles: TwelveData returns 'YYYY-MM-DD' only.
                    ts = datetime.strptime(raw_dt, "%Y-%m-%d").replace(tzinfo=UTC)
                else:
                    ts = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=ts,
                        open=Decimal(row["open"]),
                        high=Decimal(row["high"]),
                        low=Decimal(row["low"]),
                        close=Decimal(row["close"]),
                        volume=Decimal(row.get("volume", "0") or "0"),
                    )
                )
            except (ValueError, KeyError) as exc:
                logger.warning("skipping invalid TwelveData row", error=str(exc))

        # TwelveData returns newest first — reverse to ascending.
        candles.sort(key=lambda c: c.ts)

        logger.info(
            "twelvedata ohlcv fetched",
            symbol=symbol,
            timeframe=timeframe.value,
            count=len(candles),
            since=since_utc.isoformat(),
        )
        return candles

    @retry(
        retry=retry_if_exception_type(RateLimitExceeded),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def fetch_quote(self, symbol: str) -> Quote:
        """Fetch real-time price from TwelveData."""
        await self._limiter.acquire(TWELVEDATA_BUCKET)

        try:
            resp = await self._http.get(
                "/price",
                params={"symbol": symbol, "apikey": self._api_key},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"TwelveData quote error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("TwelveData 429 on quote")
        if resp.status_code >= 400:
            raise ProviderError(f"TwelveData quote HTTP {resp.status_code}")

        body = resp.json()
        if "price" not in body:
            raise ProviderError(f"TwelveData quote missing price: {body}")

        return Quote(
            symbol=symbol,
            price=Decimal(body["price"]),
            ts=datetime.now(UTC),
        )

    async def close(self) -> None:
        await self._http.aclose()
