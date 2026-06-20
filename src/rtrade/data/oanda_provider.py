"""OANDA v20 REST market-data provider (FX + metals incl. XAU_USD).

One instance = one OANDA account/token (a single composite "leg"). Practice and
live share the v20 API shape; only the host differs. Uses mid prices (price=M).
Each instance rate-limits through its own Redis token bucket so multiple
accounts back off independently.
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
from rtrade.data.ratelimit import OANDA_BUCKET, BucketConfig, RateLimiter

logger = structlog.get_logger(__name__)

OANDA_PRACTICE_URL = "https://api-fxpractice.oanda.com"
OANDA_LIVE_URL = "https://api-fxtrade.oanda.com"

_TF_MAP: dict[Timeframe, str] = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D",
}


def _parse_oanda_time(raw: str) -> datetime:
    """RFC3339 with up to 9 fractional digits + 'Z' → UTC bar-open datetime."""
    return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


class OandaProvider(MarketDataProvider):
    """XAU_USD / FX OHLCV + quote via OANDA v20 REST (one account per instance)."""

    def __init__(
        self,
        token: str,
        account_id: str,
        rate_limiter: RateLimiter,
        *,
        bucket: BucketConfig = OANDA_BUCKET,
        practice: bool = True,
        http_timeout: float = 15.0,
    ) -> None:
        if not token:
            raise ProviderError("OANDA token is required")
        self._account_id = account_id
        self._limiter = rate_limiter
        self._bucket = bucket
        base = OANDA_PRACTICE_URL if practice else OANDA_LIVE_URL
        self._http = httpx.AsyncClient(
            base_url=base,
            timeout=http_timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept-Datetime-Format": "RFC3339",
                "User-Agent": "RobilTrade/0.1",
            },
        )

    @retry(
        retry=retry_if_exception_type(RateLimitExceeded),
        wait=wait_exponential(multiplier=2, min=2, max=20),
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
        gran = _TF_MAP.get(timeframe)
        if gran is None:
            raise ProviderError(f"unsupported timeframe for OANDA: {timeframe}")
        await self._limiter.acquire(self._bucket)
        since_utc = ensure_utc(since)
        params: dict[str, str | int] = {
            "granularity": gran,
            "price": "M",
            "from": since_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": min(limit, 5000),
        }
        try:
            resp = await self._http.get(f"/v3/instruments/{symbol}/candles", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OANDA HTTP error: {exc}") from exc
        if resp.status_code == 429:
            raise RateLimitExceeded("OANDA 429: rate limit hit")
        if resp.status_code >= 400:
            raise ProviderError(f"OANDA HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        candles: list[Candle] = []
        for row in body.get("candles", []):
            if not row.get("complete", False):
                continue
            mid = row.get("mid", {})
            try:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        ts=_parse_oanda_time(row["time"]),
                        open=Decimal(mid["o"]),
                        high=Decimal(mid["h"]),
                        low=Decimal(mid["l"]),
                        close=Decimal(mid["c"]),
                        volume=Decimal(str(row.get("volume", 0))),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("skipping invalid OANDA candle", error=str(exc))
        candles.sort(key=lambda c: c.ts)
        logger.info(
            "oanda ohlcv fetched",
            symbol=symbol,
            timeframe=timeframe.value,
            count=len(candles),
        )
        return candles

    async def _pricing(self, symbol: str) -> dict[str, object]:
        resp = await self._http.get(
            f"/v3/accounts/{self._account_id}/pricing",
            params={"instruments": symbol},
        )
        if resp.status_code == 429:
            raise RateLimitExceeded("OANDA 429 on pricing")
        if resp.status_code >= 400:
            raise ProviderError(f"OANDA pricing HTTP {resp.status_code}")
        body: dict[str, object] = resp.json()
        return body

    async def fetch_quote(self, symbol: str) -> Quote:
        await self._limiter.acquire(self._bucket)
        try:
            body = await self._pricing(symbol)
        except httpx.HTTPError as exc:
            raise ProviderError(f"OANDA quote error: {exc}") from exc
        prices = body.get("prices", [])
        if not isinstance(prices, list) or not prices:
            raise ProviderError(f"OANDA pricing empty for {symbol}")
        p = prices[0]
        bid = Decimal(str(p["bids"][0]["price"]))
        ask = Decimal(str(p["asks"][0]["price"]))
        return Quote(symbol=symbol, price=(bid + ask) / 2, ts=datetime.now(UTC))

    async def fetch_spread(self, symbol: str) -> float | None:
        await self._limiter.acquire(self._bucket)
        try:
            body = await self._pricing(symbol)
        except (httpx.HTTPError, ProviderError, RateLimitExceeded):
            return None
        prices = body.get("prices", [])
        if not isinstance(prices, list) or not prices:
            return None
        p = prices[0]
        return float(Decimal(str(p["asks"][0]["price"])) - Decimal(str(p["bids"][0]["price"])))

    async def close(self) -> None:
        await self._http.aclose()
