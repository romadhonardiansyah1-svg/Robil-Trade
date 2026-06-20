"""Preservation tests for BUG 4 & BUG 5 fixes — ingestion & calendar runtime.

Property 9 (Preservation): Ingestion First-Run / Stale Watermark and Calendar
Runtime.

These tests capture BASELINE behavior that already holds on the UNFIXED code and
that the BUG 5 (freshness short-circuit) and BUG 4 (calendar type-only) fixes
MUST NOT regress. Written observation-first: assertions mirror behavior observed
on the current code.

Scope (from design.md Preservation Requirements / bugfix.md 3.7-3.11):
  - First-run ingestion backfills (since = now - 120 days, limit = 500, one call).
  - Stale-watermark ingestion fetches incremental (since = watermark - 2 bars,
    limit = 10, one call).
  - Calendar parsing / impact normalization / 429 handling are functionally
    identical (the BUG 4 fix only touches type annotations).
  - Non-crypto stale calendar still fails CLOSE (GR-07b rejects).
  - System stays signal-only with llm.enabled = false, deterministic.

NOTE (observation-first): the BUG 5 fresh-watermark short-circuit is the bug
condition and is covered by the exploration test. Here we only assert STALE
watermark ages (>= 1 bar) and first-run, which call the provider on BOTH the
unfixed and fixed code, so these tests pass now and stay green after the fix.

**Validates: Requirements 3.7, 3.8, 3.9, 3.10, 3.11**
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from hypothesis import given, settings
from hypothesis import strategies as st
import pytest
import respx
import yaml

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Action, Market, Timeframe
from rtrade.core.errors import RateLimitExceeded
from rtrade.core.timeutil import timeframe_duration
from rtrade.data.base import Candle, MarketDataProvider
from rtrade.data.investing_calendar import (
    _normalize_impact as investing_normalize_impact,
)
from rtrade.data.nasdaq_calendar import NasdaqCalendarProvider
from rtrade.data.nasdaq_calendar import _normalize_impact as nasdaq_normalize_impact
from rtrade.guardrails.gate import run_gate
from rtrade.persistence.repositories import CandleRow
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime(2026, 6, 11, 10, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes for _ingest_incremental (mirrors tests/unit/test_ingest_incremental.py)
# ---------------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, latest_candle: Any = None) -> None:
        self._latest = latest_candle
        self.upserts: list[CandleRow] = []

    async def latest(self, instrument_id: int, tf: Timeframe) -> Any:
        return self._latest

    async def upsert_many(self, rows: list[CandleRow]) -> int:
        self.upserts.extend(rows)
        return len(rows)


class _FakeProvider(MarketDataProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Candle]:
        self.calls.append({"since": since, "limit": limit})
        return []

    async def fetch_quote(self, symbol: str) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - unused
        pass


def _make_instrument() -> InstrumentConfig:
    return InstrumentConfig(
        symbol="XAUUSD",
        market=Market.METALS,
        provider="twelvedata",
        provider_symbol="XAU/USD",
        timeframes=[Timeframe.H1],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


# ---------------------------------------------------------------------------
# 3.7 — first-run backfill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_run_backfills_120_days_one_call() -> None:
    """latest is None => one provider call, limit=500, since ~= now - 120 days."""
    from rtrade.pipeline.scan import _ingest_incremental

    provider = _FakeProvider()
    repo = _FakeRepo(latest_candle=None)

    await _ingest_incremental(
        provider,
        _make_instrument(),
        1,
        Timeframe.H1,
        repo,  # type: ignore[arg-type]
        _NOW,
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["limit"] == 500
    assert abs((_NOW - timedelta(days=120)) - call["since"]) < timedelta(minutes=1)


# ---------------------------------------------------------------------------
# 3.8 — stale watermark incremental
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_watermark_incremental_one_call() -> None:
    """Stale watermark => one call, limit=10, since = watermark - 2 bars."""
    from rtrade.pipeline.scan import _ingest_incremental

    class _StaleCandle:
        ts = datetime(2026, 6, 11, 8, 0, tzinfo=UTC)  # 2h old for H1 => stale

    provider = _FakeProvider()
    repo = _FakeRepo(latest_candle=_StaleCandle())

    await _ingest_incremental(
        provider,
        _make_instrument(),
        1,
        Timeframe.H1,
        repo,  # type: ignore[arg-type]
        _NOW,
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["limit"] == 10
    assert call["since"] == datetime(2026, 6, 11, 6, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 3.8 — property-based: any stale watermark (age >= 2 bars) fetches once
# ---------------------------------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(
    age_bars=st.integers(min_value=2, max_value=200),
    tf=st.sampled_from([Timeframe.H1, Timeframe.H4, Timeframe.D1]),
)
def test_stale_watermark_always_fetches_once(age_bars: int, tf: Timeframe) -> None:
    """For any clearly-stale watermark, the provider is called exactly once.

    Generates ages >= 2 bars to stay outside the BUG 5 fresh-watermark region
    (< 1 bar), so the behavior is identical on unfixed and fixed code: one fetch
    with limit=10 and since = watermark - 2 bars.
    """
    duration = timeframe_duration(tf)
    watermark_ts = _NOW - age_bars * duration

    class _Candle:
        ts = watermark_ts

    async def _run() -> None:
        from rtrade.pipeline.scan import _ingest_incremental

        provider = _FakeProvider()
        repo = _FakeRepo(latest_candle=_Candle())
        await _ingest_incremental(
            provider,
            _make_instrument(),
            1,
            tf,
            repo,  # type: ignore[arg-type]
            _NOW,
        )
        assert len(provider.calls) == 1
        assert provider.calls[0]["limit"] == 10
        assert provider.calls[0]["since"] == watermark_ts - 2 * duration

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 3.10 — calendar impact normalization unchanged (runtime, not types)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_impact", "event_name", "expected"),
    [
        ("high", "Some Event", "high"),
        ("medium", "Some Event", "medium"),
        ("low", "Some Event", "low"),
        (3, "Some Event", "high"),
        (2, "Some Event", "medium"),
        (1, "Some Event", "low"),
        ("low", "US CPI release", "high"),  # keyword override -> high
        ("low", "FOMC statement", "high"),
    ],
)
def test_nasdaq_normalize_impact_unchanged(
    raw_impact: str | int, event_name: str, expected: str
) -> None:
    assert nasdaq_normalize_impact(raw_impact, event_name) == expected


@pytest.mark.parametrize(
    ("raw_impact", "event_name", "expected"),
    [
        ("high", "Some Event", "high"),
        ("bullish", "Some Event", "high"),  # investing-specific mapping
        ("medium", "Some Event", "medium"),
        ("low", "Some Event", "low"),
        (3, "Some Event", "high"),
        (2, "Some Event", "medium"),
        (1, "Some Event", "low"),
        ("low", "Nonfarm Payrolls", "high"),  # keyword override -> high
    ],
)
def test_investing_normalize_impact_unchanged(
    raw_impact: str | int, event_name: str, expected: str
) -> None:
    assert investing_normalize_impact(raw_impact, event_name) == expected


# ---------------------------------------------------------------------------
# 3.10 — calendar 429 handling + parsing unchanged (Nasdaq runtime)
# ---------------------------------------------------------------------------

_NASDAQ_URL = "https://data.nasdaq.com/api/v3/datatables/NDAQ/ECONCALENDAR"


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_429_raises_rate_limit() -> None:
    """A 429 response is still mapped to RateLimitExceeded (unchanged handling)."""
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(429))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        with pytest.raises(RateLimitExceeded):
            await provider.fetch_events(date(2026, 7, 1), date(2026, 7, 2))
    finally:
        await provider.close()


@pytest.mark.asyncio
@respx.mock
async def test_nasdaq_parses_events_unchanged() -> None:
    """A normal 200 datatable payload still parses to normalized events."""
    payload = {
        "datatable": {
            "columns": [
                {"name": "event"},
                {"name": "country"},
                {"name": "impact"},
                {"name": "date"},
                {"name": "time"},
            ],
            "data": [
                ["FOMC Meeting", "US", "high", "2026-07-30", "18:00:00"],
                ["Retail Sales", "GB", "medium", "2026-07-15", "08:30:00"],
            ],
        }
    }
    respx.get(_NASDAQ_URL).mock(return_value=httpx.Response(200, json=payload))
    provider = NasdaqCalendarProvider(api_key="testkey")
    try:
        events = await provider.fetch_events(date(2026, 7, 1), date(2026, 7, 31))
    finally:
        await provider.close()

    assert len(events) == 2
    by_currency = {e.currency: e for e in events}
    assert by_currency["USD"].impact == "high"  # FOMC keyword + "high"
    assert by_currency["GBP"].impact == "medium"


# ---------------------------------------------------------------------------
# 3.9 — non-crypto stale calendar fails CLOSE (GR-07b)
# ---------------------------------------------------------------------------


def _valid_candidate() -> SignalCandidate:
    return SignalCandidate(
        candidate_id="preservation",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        strategy="ema_cross",
        action=Action.BUY,
        levels=LevelSet(
            entry_limit=2000.0, stop_loss=1990.0, take_profit=2020.0, atr_at_signal=5.0
        ),
        confluence_score=70,
        confluence_breakdown=ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=_NOW,
        bar_ts=_NOW,
        created_at=_NOW,
    )


def test_stale_calendar_fails_close() -> None:
    """A stale calendar makes run_gate REJECT (fail-CLOSE via GR-07b)."""
    result = run_gate(_valid_candidate(), calendar_stale=True)
    assert not result.passed
    assert any(f.gate_id == "GR-07" for f in result.failures)


# ---------------------------------------------------------------------------
# 3.11 — signal-only, llm disabled, deterministic
# ---------------------------------------------------------------------------


def test_llm_disabled_in_settings() -> None:
    """The shipped settings keep llm.enabled = false (signal-only baseline)."""
    settings_path = _REPO_ROOT / "config" / "settings.yaml"
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    assert data["llm"]["enabled"] is False


def test_no_order_execution_in_source() -> None:
    """Signal-only invariant: no broker order-execution calls anywhere in src."""
    forbidden = (
        "create_order",
        "place_order",
        "submit_order",
        "create_market_order",
        "create_limit_order",
    )
    src_root = _REPO_ROOT / "src" / "rtrade"
    offenders: list[str] = []
    for py_file in src_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert offenders == [], f"order-execution code found (violates signal-only): {offenders}"
