"""Investing.com economic-calendar provider (independent re-implementation).

Low-dependency httpx JSON client behind the CalendarProvider ABC. Replaces the
paid-only Finnhub /calendar/economic (HTTP 403 on free tier). Endpoint choice and
Terms-of-Service rationale are documented in ADR-A12. No Selenium/browser
automation. All parsing re-implemented from the public endpoint shape; no
third-party GPL/AGPL code (ADR-A10).
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
from rtrade.core.text_sanitize import sanitize_event_text
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.investing.com"
_DEFAULT_TIMEOUT = 15.0

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
    return _COUNTRY_TO_CURRENCY.get(raw.strip().upper(), raw.strip().upper())


def _normalize_impact(raw_impact: str | int, event_name: str) -> str:
    name_lower = event_name.lower()
    for kw in _ALWAYS_HIGH_EVENTS:
        if kw in name_lower:
            return "high"
    if isinstance(raw_impact, int):
        if raw_impact >= 3:
            return "high"
        if raw_impact == 2:
            return "medium"
        return "low"
    s = str(raw_impact).lower()
    if s in ("high", "3", "bullish"):
        return "high"
    if s in ("medium", "2"):
        return "medium"
    return "low"


def _event_id(event_name: str, event_time: str, currency: str) -> str:
    return hashlib.sha256(f"investing:{event_name}:{event_time}:{currency}".encode()).hexdigest()[
        :16
    ]


def _parse_event_time(date_str: str, time_str: str) -> datetime:
    """Parse a calendar date/time into a UTC-aware datetime.

    Tries the strict "%Y-%m-%d %H:%M:%S" format first (naive treated as UTC) to
    preserve existing behavior, then falls back to ISO-8601 parsing (handling a
    trailing 'Z' and offsets). Raises ValueError if neither format applies.
    """
    full_dt_str = f"{date_str} {time_str}"
    try:
        return datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        pass
    normalized = full_dt_str.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_decimal(val: object) -> Decimal | None:
    if val is None or val == "":
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None


class InvestingCalendarProvider(CalendarProvider):
    """FR-CAL-01. Keyless. Transient-only retry. Sanitizes event names."""

    def __init__(self, *, http_timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=http_timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Domain-ID": "www",
            },
        )

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, str]) -> httpx.Response:
        return await self._http.get(path, params=params)

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        params: dict[str, str] = {
            "dateFrom": start.isoformat(),
            "dateTo": end.isoformat(),
            "timeframe": "60",
        }
        try:
            resp = await self._get("/api/financialcalendar", params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Investing calendar HTTP error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("Investing 429")
        if resp.status_code in (401, 403):
            raise ProviderError(f"Investing {resp.status_code}: denied")
        if resp.status_code >= 400:
            raise ProviderError(f"Investing HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            # JSONDecodeError (a ValueError subclass): a non-2xx-but-unparseable
            # body is a provider failure, NOT an empty calendar.
            raise ProviderError(f"Investing calendar: invalid JSON body: {exc}") from exc

        if not isinstance(body, dict):
            raise ProviderError(
                f"Investing calendar: unexpected response shape: {type(body).__name__}"
            )

        # Locate the events container. A genuinely-empty calendar has the key
        # present with an empty list; a response with NO recognized container is
        # schema drift and must RAISE (else a broken source masquerades as empty).
        raw_events = body.get("data")
        if raw_events is None:
            raw_events = body.get("events")
        if raw_events is None:
            raw_events = body.get("economicCalendar")
        if raw_events is None:
            raise ProviderError(
                "Investing calendar: response missing events container (schema drift)"
            )
        if not isinstance(raw_events, list):
            raise ProviderError("Investing calendar: events container is not a list (schema drift)")
        if not raw_events:
            logger.info(
                "Investing returned no events",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            return []

        events: list[DomainEvent] = []
        now = datetime.now(UTC)
        for row in raw_events:
            try:
                event_name = sanitize_event_text(str(row.get("event", "") or ""))
                currency = _to_currency(str(row.get("country") or row.get("currency") or ""))
                impact = _normalize_impact(
                    str(row.get("impact", row.get("importance", 1))), event_name
                )
                date_str = str(row.get("date", ""))
                time_str = str(row.get("time", "00:00:00"))
                if not event_name or not date_str:
                    continue
                full_dt_str = f"{date_str} {time_str}"
                event_time = _parse_event_time(date_str, time_str)
                events.append(
                    DomainEvent(
                        event_id=_event_id(event_name, full_dt_str, currency),
                        event=event_name,
                        currency=currency,
                        impact=impact,
                        event_time=event_time,
                        actual=_safe_decimal(row.get("actual")),
                        forecast=_safe_decimal(row.get("forecast") or row.get("estimate")),
                        previous=_safe_decimal(row.get("previous") or row.get("prev")),
                        fetched_at=now,
                    )
                )
            except (ValueError, KeyError) as exc:
                logger.warning("skipping invalid Investing event", error=str(exc), row=row)

        logger.info(
            "investing calendar fetched",
            start=start.isoformat(),
            end=end.isoformat(),
            total=len(events),
            high_impact=sum(1 for e in events if e.impact == "high"),
        )
        return events

    async def close(self) -> None:
        await self._http.aclose()
