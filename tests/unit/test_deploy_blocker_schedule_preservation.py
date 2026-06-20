"""Preservation tests for BUG 2 & BUG 3 fixes — scan success & schedule shape.

Property 8 (Preservation): Scan Success and Below-Threshold/Stagger Behavior.

These tests capture BASELINE behavior that already holds on the UNFIXED code and
that the BUG 2 (alert) and BUG 3 (scheduling) fixes MUST NOT regress. Written
observation-first: every assertion mirrors behavior observed on the current code.

Scope (from design.md Preservation Requirements / bugfix.md 3.4, 3.5, 3.6):
  - On scan success, ``scan_job`` resets ``_fail_counts[key] = 0`` and sends no
    alert.
  - ``build_scan_schedules`` emits exactly one entry per instrument x timeframe
    (4 entries for 2 instruments x 2 TFs).
  - Non-TwelveData instruments still have their seconds staggered.

NOTE (observation-first): the unfixed ``jobs`` module has no ``_last_alert_at``
attribute (removed by BUG 2). These preservation tests therefore depend ONLY on
``_fail_counts`` so they pass on the unfixed code; the ``_last_alert_at`` /
cooldown behavior is covered by the exploration tests for tasks 2 and 3.

**Validates: Requirements 3.4, 3.5, 3.6**
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st
import pytest

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.scheduler import jobs
from rtrade.scheduler.main import build_scan_schedules


@pytest.fixture(autouse=True)
def _reset_fail_counts() -> None:
    """Reset only state that exists on the UNFIXED code (no _last_alert_at)."""
    jobs._fail_counts.clear()
    # _last_alert_at is reintroduced by the BUG 2 fix; clear it only if present so
    # this fixture works both before and after the fix.
    last_alert = getattr(jobs, "_last_alert_at", None)
    if last_alert is not None:
        last_alert.clear()


# ---------------------------------------------------------------------------
# 3.4 — scan success resets _fail_counts and sends no alert
# ---------------------------------------------------------------------------


class _OkResult:
    status = "ok"
    signal_id = None
    failures: list[str] = []


@pytest.mark.asyncio
async def test_scan_success_resets_fail_counts_and_sends_no_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful scan zeroes the failure counter and triggers no alert."""

    async def ok_scan(*args: Any, **kwargs: Any) -> _OkResult:
        return _OkResult()

    alerts: list[str] = []

    async def collect_alert(message: str) -> None:
        alerts.append(message)

    monkeypatch.setattr(jobs, "run_scan", ok_scan)
    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)

    # Pre-seed a non-zero failure count to prove success resets it.
    jobs._fail_counts["USDJPY:1h"] = 2

    await jobs.scan_job("USDJPY", "1h")

    assert jobs._fail_counts["USDJPY:1h"] == 0
    assert alerts == []


# ---------------------------------------------------------------------------
# 3.5 / 3.6 — schedule count and non-TwelveData second stagger
# ---------------------------------------------------------------------------


def _make_inst(
    symbol: str,
    timeframes: list[Timeframe],
    *,
    provider: str = "twelvedata",
    market: Market = Market.METALS,
) -> InstrumentConfig:
    return InstrumentConfig(
        symbol=symbol,
        market=market,
        provider=provider,
        provider_symbol=f"{symbol[:3]}/{symbol[3:]}",
        timeframes=timeframes,
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


def test_build_scan_schedules_emits_four_entries_for_two_by_two() -> None:
    """2 instruments x 2 TFs => exactly 4 schedule entries (3.5)."""
    instruments = [
        _make_inst("XAUUSD", [Timeframe.H1, Timeframe.H4]),
        _make_inst("EURUSD", [Timeframe.H1, Timeframe.H4]),
    ]
    schedules = build_scan_schedules(instruments)
    assert len(schedules) == 4


def test_non_twelvedata_seconds_staggered() -> None:
    """Non-TwelveData instruments keep distinct staggered seconds (3.6)."""
    instruments = [
        _make_inst("BTCUSDT", [Timeframe.H1], provider="ccxt_binance", market=Market.CRYPTO),
        _make_inst("ETHUSDT", [Timeframe.H1], provider="ccxt_binance", market=Market.CRYPTO),
    ]
    schedules = build_scan_schedules(instruments)
    second_0 = schedules[0][2]["second"]
    second_1 = schedules[1][2]["second"]
    assert second_0 != second_1


# ---------------------------------------------------------------------------
# 3.5 — property-based: exactly one entry per instrument x timeframe
# ---------------------------------------------------------------------------

_SYMBOL = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=3, max_size=6)
_TIMEFRAMES = st.lists(
    st.sampled_from([Timeframe.H1, Timeframe.H4, Timeframe.D1]),
    min_size=1,
    max_size=3,
    unique=True,
)
_PROVIDER = st.sampled_from(["twelvedata", "ccxt_binance"])


@settings(max_examples=60, deadline=None)
@given(
    specs=st.lists(
        st.tuples(_SYMBOL, _TIMEFRAMES, _PROVIDER),
        min_size=1,
        max_size=5,
    )
)
def test_one_schedule_entry_per_instrument_timeframe(
    specs: list[tuple[str, list[Timeframe], str]],
) -> None:
    """For any instrument set, build_scan_schedules emits one entry per (inst, TF).

    This count invariant holds identically on the unfixed and fixed code (the
    BUG 3 fix only changes the cron minute/second values, not how many entries
    are produced).
    """
    instruments = [_make_inst(symbol, tfs, provider=provider) for symbol, tfs, provider in specs]
    schedules = build_scan_schedules(instruments)

    expected = sum(len(tfs) for _sym, tfs, _prov in specs)
    assert len(schedules) == expected
    # Every entry carries a cron mapping with both minute and second keys.
    for _symbol, _tf, cron in schedules:
        assert "minute" in cron
        assert "second" in cron
