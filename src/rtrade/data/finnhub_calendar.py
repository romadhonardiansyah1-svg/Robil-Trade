"""Finnhub economic calendar provider (PLAN §8.1, ADR-03).

Fetches economic events and normalizes impact levels. Always-high events
(FOMC, NFP, CPI US, ECB rate decision) are hardcoded per PLAN §8.7.

Sync 2×/day at 00:15 and 12:15 UTC (scheduled by scheduler module).
Free tier: 60 req/min — bucket set to 50/min.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import hashlib

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent
from rtrade.data.ratelimit import FINNHUB_BUCKET, RateLimiter

logger = structlog.get_logger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"

# Events that are ALWAYS classified as high impact, regardless of what the
# provider reports. These are the market-moving events per PLAN §8.7.
_ALWAYS_HIGH_EVENTS: set[str] = {
    "fomc",
    "federal funds rate",
    "fed interest rate decision",
    "nonfarm payrolls",
    "non-farm payrolls",
    "nfp",
    "cpi",
    "consumer price index",
    "ecb interest rate decision",
    "ecb rate decision",
    "ecb monetary policy",
}

# Finnhub returns COUNTRY codes; the news filter compares CURRENCY codes.
_COUNTRY_TO_CURRENCY: dict[str, str] = {
    "US": "USD",
    "EU": "EUR",
    "EZ": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "GB": "GBP",
    "UK": "GBP",
    "JP": "JPY",
    "CH": "CHF",
    "CA": "CAD",
    "AU": "AUD",
    "NZ": "NZD",
    "CN": "CNY",
}


def _to_currency(raw: str) -> str:
    """Map a Finnhub country code to a currency code; pass through unknowns."""
    code = raw.strip().upper()
    return _COUNTRY_TO_CURRENCY.get(code, code)


def _normalize_impact(raw_impact: str | int, event_name: str) -> str:
    """Normalize Finnhub impact to low/medium/high.

    Finnhub uses 1/2/3 numeric scale. We also override known events.
    """
    name_lower = event_name.lower()
    for keyword in _ALWAYS_HIGH_EVENTS:
        if keyword in name_lower:
            return "high"

    if isinstance(raw_impact, int):
        if raw_impact >= 3:
            return "high"
        if raw_impact == 2:
            return "medium"
        return "low"

    impact_str = str(raw_impact).lower()
    if impact_str in ("high", "3"):
        return "high"
    if impact_str in ("medium", "2"):
        return "medium"
    return "low"


def _event_id(event_name: str, event_time: str, currency: str) -> str:
    """Deterministic ID for dedup (PLAN §10: id = hash(provider,event,time))."""
    raw = f"finnhub:{event_name}:{event_time}:{currency}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _safe_decimal(val: object) -> Decimal | None:
    """Convert to Decimal, returning None for missing/invalid values."""
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


class FinnhubCalendarProvider(CalendarProvider):
    """Economic calendar via Finnhub REST API (PLAN §8.1).

    Requires FINNHUB_API_KEY. Falls back to empty list if API is unavailable
    (fail-CLOSED for sinyal: no calendar data → news_filter blocks
    forex/metals signals as a safety measure — PLAN §15 risk #6).
    """

    def __init__(
        self,
        api_key: str,
        rate_limiter: RateLimiter,
        *,
        http_timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise ProviderError("FINNHUB_API_KEY is required")
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
    async def fetch_events(
        self,
        start: date,
        end: date,
    ) -> list[DomainEvent]:
        """Fetch economic calendar events from Finnhub."""
        await self._limiter.acquire(FINNHUB_BUCKET)

        try:
            resp = await self._http.get(
                "/calendar/economic",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "token": self._api_key,
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Finnhub calendar HTTP error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("Finnhub 429: rate limit hit")
        if resp.status_code == 401:
            raise ProviderError("Finnhub 401: invalid API key")
        if resp.status_code >= 400:
            raise ProviderError(f"Finnhub HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Finnhub calendar: invalid JSON body: {exc}") from exc

        if not isinstance(body, dict):
            raise ProviderError(
                f"Finnhub calendar: unexpected response shape: {type(body).__name__}"
            )
        # A genuinely-empty calendar returns {"economicCalendar": []}; a response
        # missing the key entirely is schema drift and must RAISE (not masked []).
        if "economicCalendar" not in body:
            raise ProviderError(
                "Finnhub calendar: response missing 'economicCalendar' key (schema drift)"
            )
        raw_events = body["economicCalendar"]
        if not isinstance(raw_events, list):
            raise ProviderError("Finnhub calendar: 'economicCalendar' is not a list (schema drift)")

        if not raw_events:
            logger.info("Finnhub returned no events", start=start.isoformat(), end=end.isoformat())
            return []

        events: list[DomainEvent] = []
        now = datetime.now(UTC)

        for row in raw_events:
            try:
                event_name = row.get("event", "")
                currency = str(row.get("country") or row.get("currency") or "")
                impact = row.get("impact", row.get("importance", 1))
                time_str = row.get("time", "00:00:00")
                date_str = row.get("date", "")

                if not event_name or not date_str:
                    continue

                # Parse event datetime (Finnhub: date=YYYY-MM-DD, time=HH:MM:SS)
                full_dt_str = f"{date_str} {time_str}"
                event_time = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

                events.append(
                    DomainEvent(
                        event_id=_event_id(event_name, full_dt_str, currency),
                        event=event_name,
                        currency=_to_currency(currency),
                        impact=_normalize_impact(impact, event_name),
                        event_time=event_time,
                        actual=_safe_decimal(row.get("actual")),
                        forecast=_safe_decimal(row.get("estimate")),
                        previous=_safe_decimal(row.get("prev")),
                        fetched_at=now,
                    )
                )
            except (ValueError, KeyError) as exc:
                logger.warning("skipping invalid Finnhub event", error=str(exc), row=row)

        logger.info(
            "finnhub calendar fetched",
            start=start.isoformat(),
            end=end.isoformat(),
            total=len(events),
            high_impact=sum(1 for e in events if e.impact == "high"),
        )
        return events

    async def close(self) -> None:
        await self._http.aclose()
