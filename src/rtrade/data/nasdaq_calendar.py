"""Nasdaq Data Link economic-calendar provider (independent re-implementation).

Secondary source tipe berbeda dari Investing (FR-CAL-02). httpx + transient-only
retry. Falls back gracefully if API key not configured.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import os

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
_BASE_URL = "https://data.nasdaq.com/api/v3"

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
    high_keywords = {
        "fomc",
        "federal funds rate",
        "nonfarm payrolls",
        "non-farm payrolls",
        "nfp",
        "cpi",
        "consumer price index",
        "ecb interest rate decision",
        "ecb rate decision",
    }
    for kw in high_keywords:
        if kw in name_lower:
            return "high"
    if isinstance(raw_impact, int):
        if raw_impact >= 3:
            return "high"
        if raw_impact == 2:
            return "medium"
        return "low"
    s = str(raw_impact).lower()
    if s in ("high", "3"):
        return "high"
    if s in ("medium", "2"):
        return "medium"
    return "low"


def _event_id(event_name: str, event_time: str, currency: str) -> str:
    return hashlib.sha256(f"nasdaq:{event_name}:{event_time}:{currency}".encode()).hexdigest()[:16]


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


class NasdaqCalendarProvider(CalendarProvider):
    """Nasdaq Data Link economic calendar (FR-CAL-02). Transient-only retry."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or os.environ.get("NDAQ_API_KEY", "")
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=http_timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "RobilTrade/0.1",
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
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        if self._api_key:
            params["api_key"] = self._api_key
        try:
            resp = await self._get("/datatables/NDAQ/ECONCALENDAR", params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Nasdaq HTTP error: {exc}") from exc

        if resp.status_code == 429:
            raise RateLimitExceeded("Nasdaq 429")
        if resp.status_code in (401, 403):
            raise ProviderError(f"Nasdaq {resp.status_code}: auth/access denied")
        if resp.status_code >= 400:
            raise ProviderError(f"Nasdaq HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"Nasdaq: invalid JSON body: {exc}") from exc

        if not isinstance(body, dict):
            raise ProviderError(f"Nasdaq: unexpected response shape: {type(body).__name__}")
        datatable = body.get("datatable")
        if not isinstance(datatable, dict):
            raise ProviderError("Nasdaq: response missing 'datatable' object (schema drift)")
        raw_data = datatable.get("data")
        if raw_data is None:
            raise ProviderError("Nasdaq: 'datatable.data' missing (schema drift)")
        if not isinstance(raw_data, list):
            raise ProviderError("Nasdaq: 'datatable.data' is not a list (schema drift)")
        columns = [c.get("name", "") for c in datatable.get("columns", [])]

        if not raw_data:
            logger.info(
                "Nasdaq returned no events",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            return []

        events: list[DomainEvent] = []
        now = datetime.now(UTC)
        for row in raw_data:
            try:
                row_dict = dict(zip(columns, row, strict=False)) if columns else {}
                event_name = sanitize_event_text(
                    str(row_dict.get("event", row_dict.get("indicator", "")) or "")
                )
                currency = _to_currency(
                    str(row_dict.get("country", row_dict.get("currency", "")) or "")
                )
                impact = _normalize_impact(
                    str(row_dict.get("impact", row_dict.get("importance", 1))), event_name
                )
                date_str = str(row_dict.get("date", ""))
                time_str = str(row_dict.get("time", "00:00:00"))
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
                        actual=_safe_decimal(row_dict.get("actual")),
                        forecast=_safe_decimal(row_dict.get("forecast")),
                        previous=_safe_decimal(row_dict.get("previous")),
                        fetched_at=now,
                    )
                )
            except (ValueError, KeyError, IndexError) as exc:
                logger.warning("skipping invalid Nasdaq event", error=str(exc))

        logger.info(
            "nasdaq calendar fetched",
            start=start.isoformat(),
            end=end.isoformat(),
            total=len(events),
            high_impact=sum(1 for e in events if e.impact == "high"),
        )
        return events

    async def close(self) -> None:
        await self._http.aclose()
