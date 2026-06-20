"""Bug condition exploration test for BUG 3 — schedule burst (deploy-blocker-fixes).

Property 4 (Bug Condition): TwelveData Schedules Spread Across Minutes.

This test encodes the EXPECTED post-fix behavior described in design.md
(Correctness Property 4, Bug Condition C3) and bugfix.md requirements 1.5, 1.6,
2.5, 2.6:

    For build_scan_schedules() over the four TwelveData H1 instruments
    (XAUUSD, EURUSD, GBPUSD, USDJPY) the fixed code SHALL assign `minute` values
    ["0","10","20","30"] with all `second == "30"`; and any H4 entry SHALL use
    `minute == "5"` with `hour == "0,4,8,12,16,20"`.

On the UNFIXED code this test MUST FAIL: build_scan_schedules() packs every
TwelveData H1 instrument onto `minute="0"` (staggered only by 5 seconds:
30,35,40,45) and places H4 on `minute="0"` too. The H1 `minute` list therefore
comes out as ["0","0","0","0"] instead of the staggered ["0","10","20","30"],
and the seconds are not all "30". That failure confirms the bug exists.

DO NOT fix the code or this test when it fails — the failure is the success case
for this exploration step.

**Validates: Requirements 1.5, 1.6, 2.5, 2.6**
"""

from __future__ import annotations

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.scheduler.main import build_scan_schedules

# The four TwelveData H1 instruments from config/instruments.yaml (Bug Condition C3).
_TWELVEDATA_H1_SYMBOLS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY"]


def _make_twelvedata_inst(symbol: str, timeframes: list[Timeframe]) -> InstrumentConfig:
    """Build a TwelveData InstrumentConfig matching config/instruments.yaml shape."""
    return InstrumentConfig(
        symbol=symbol,
        market=Market.FOREX,
        provider="twelvedata",
        provider_symbol=f"{symbol[:3]}/{symbol[3:]}",
        timeframes=timeframes,
        context_timeframe=Timeframe.D1,
        pip_size=0.0001,
        quote_currency="USD",
    )


def test_twelvedata_h1_schedules_spread_across_minutes() -> None:
    """H1 minutes == ["0","10","20","30"] with every second == "30" (Property 4).

    Scoped to the four TwelveData H1 instruments. On the fixed code the per-minute
    stagger spreads the burst across the hour; on the unfixed code all four land on
    minute="0" with 5-second seconds → ["0","0","0","0"], reproducing BUG 3.
    """
    instruments = [_make_twelvedata_inst(sym, [Timeframe.H1]) for sym in _TWELVEDATA_H1_SYMBOLS]

    schedules = build_scan_schedules(instruments)

    minutes = [cron["minute"] for _symbol, _tf, cron in schedules]
    seconds = {cron["second"] for _symbol, _tf, cron in schedules}

    assert minutes == ["0", "10", "20", "30"], (
        "BUG 3 reproduced: TwelveData H1 instruments are not spread across minutes; "
        f"got minute list {minutes} (unfixed code packs all on minute='0')."
    )
    assert seconds == {"30"}, (
        "BUG 3 reproduced: TwelveData H1 seconds are not all '30'; "
        f"got {sorted(seconds)} (unfixed code staggers seconds 30,35,40,45)."
    )


def test_twelvedata_h4_schedule_uses_minute_five() -> None:
    """Any H4 entry uses minute == "5" with hour == "0,4,8,12,16,20" (Property 4).

    On the unfixed code H4 shares minute="0" with the H1 burst, reproducing BUG 3.
    """
    instruments = [
        _make_twelvedata_inst(sym, [Timeframe.H1, Timeframe.H4]) for sym in _TWELVEDATA_H1_SYMBOLS
    ]

    schedules = build_scan_schedules(instruments)

    h4_entries = [cron for _symbol, tf, cron in schedules if tf == Timeframe.H4.value]
    assert h4_entries, "expected at least one H4 schedule entry"

    for cron in h4_entries:
        assert cron["minute"] == "5", (
            "BUG 3 reproduced: H4 entry does not use minute='5'; "
            f"got minute={cron['minute']!r} (unfixed code uses '0')."
        )
        assert cron["hour"] == "0,4,8,12,16,20", (
            f"H4 entry should run on 4h hours; got hour={cron['hour']!r}."
        )
