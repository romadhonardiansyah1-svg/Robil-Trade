"""Failure-mode tests for calendar source providers (B1 residual).

A source provider that silently returns ``[]`` on a parse/HTTP failure defeats
the composite's failover and the downstream fail-CLOSED staleness gate (a broken
source looks "empty" instead of "failed"). These tests pin the required
contract per provider:

  * HTTP non-2xx / network error  -> raise ``ProviderError`` (never return []).
  * Body that cannot be parsed / unexpected shape (schema drift) -> raise
    ``ProviderError`` (never silently return []).
  * A VALID response that parses but has zero in-range events -> return []
    (legitimate empty; NOT an error).

Per-event tolerance (a single malformed row inside an otherwise-valid list may
be skipped-with-warning) is intentionally NOT asserted here as a failure: that
is covered by the existing parse tests. A WHOLESALE parse failure must raise.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from rtrade.core.errors import ProviderError
from rtrade.data.finnhub_calendar import FinnhubCalendarProvider
from rtrade.data.investing_calendar import InvestingCalendarProvider
from rtrade.data.nasdaq_calendar import NasdaqCalendarProvider
from rtrade.data.static_calendar import StaticCalendarProvider

_INVESTING_URL = "https://api.investing.com/api/financialcalendar"
_NASDAQ_URL = "https://data.nasdaq.com/api/v3/datatables/NDAQ/ECONCALENDAR"
_FINNHUB_URL = "https://finnhub.io/api/v1/calendar/economic"

_START = date(2026, 7, 1)
_END = date(2026, 7, 31)


class _NoopLimiter:
    """Rate limiter stub: always allows the call (no Redis in unit tests)."""

    async def acquire(self, bucket: Any) -> None:
        return None


# ---------------------------------------------------------------------------
# Investing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_investing_http_500_raises() -> None:
    respx.get(_INVESTING_URL).mock(return_value=httpx.Response(500, text="boom"))
    provider = InvestingCalendarProvider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_investing_malformed_body_raises() -> None:
    respx.get(_INVESTING_URL).mock(return_value=httpx.Response(200, text="<<not json>>"))
    provider = InvestingCalendarProvider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_investing_unexpected_shape_raises() -> None:
    # Valid JSON but NO recognized events container (schema drift) -> must raise.
    respx.get(_INVESTING_URL).mock(return_value=httpx.Response(200, json={"foo": "bar"}))
    provider = InvestingCalendarProvider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_investing_valid_empty_returns_empty() -> None:
    # Valid response, genuinely zero events -> [] (NOT an error).
    respx.get(_INVESTING_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    provider = InvestingCalendarProvider()
    try:
        events = await provider.fetch_events(_START, _END)
    finally:
        await provider.close()
    assert events == []


# ---------------------------------------------------------------------------
# Nasdaq
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_http_500_raises() -> None:
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(500, text="boom"))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_malformed_body_raises() -> None:
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(200, text="<<not json>>"))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_missing_datatable_raises() -> None:
    # No 'datatable' object at all -> schema drift -> must raise (not masked []).
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_valid_empty_returns_empty() -> None:
    payload = {"datatable": {"data": [], "columns": [{"name": "event"}]}}
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(200, json=payload))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        events = await provider.fetch_events(_START, _END)
    finally:
        await provider.close()
    assert events == []


# ---------------------------------------------------------------------------
# Finnhub
# ---------------------------------------------------------------------------


def _finnhub_provider() -> FinnhubCalendarProvider:
    return FinnhubCalendarProvider("testkey", _NoopLimiter())  # type: ignore[arg-type]


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_http_500_raises() -> None:
    respx.get(_FINNHUB_URL).mock(return_value=httpx.Response(500, text="boom"))
    provider = _finnhub_provider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_malformed_body_raises() -> None:
    respx.get(_FINNHUB_URL).mock(return_value=httpx.Response(200, text="<<not json>>"))
    provider = _finnhub_provider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_missing_key_raises() -> None:
    # No 'economicCalendar' key -> schema drift -> must raise (not masked []).
    respx.get(_FINNHUB_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    provider = _finnhub_provider()
    try:
        with pytest.raises(ProviderError):
            await provider.fetch_events(_START, _END)
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_finnhub_valid_empty_returns_empty() -> None:
    respx.get(_FINNHUB_URL).mock(return_value=httpx.Response(200, json={"economicCalendar": []}))
    provider = _finnhub_provider()
    try:
        events = await provider.fetch_events(_START, _END)
    finally:
        await provider.close()
    assert events == []


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(ProviderError):
        provider = StaticCalendarProvider(missing)
        await provider.fetch_events(_START, _END)


@pytest.mark.asyncio
async def test_static_corrupt_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.json"
    path.write_text("<<not json>>", encoding="utf-8")
    with pytest.raises(ProviderError):
        provider = StaticCalendarProvider(path)
        await provider.fetch_events(_START, _END)


@pytest.mark.asyncio
async def test_static_malformed_event_raises(tmp_path: Path) -> None:
    # An event entry missing the required 'time' field corrupts the whole file
    # (a curated static file is fail-CLOSED: do not silently drop events).
    data = {"version": "x", "events": [{"event": "FOMC", "currency": "USD"}]}
    path = tmp_path / "bad_event.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProviderError):
        provider = StaticCalendarProvider(path)
        await provider.fetch_events(_START, _END)


@pytest.mark.asyncio
async def test_static_valid_empty_returns_empty(tmp_path: Path) -> None:
    data = {
        "version": "x",
        "events": [{"event": "FOMC", "currency": "USD", "time": "2030-07-30T18:00:00Z"}],
    }
    path = tmp_path / "valid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    provider = StaticCalendarProvider(path)
    # Out-of-range window -> valid file, zero in-range events -> [] (no raise).
    events = await provider.fetch_events(date(2020, 1, 1), date(2020, 1, 31))
    await provider.close()
    assert events == []
