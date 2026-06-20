from __future__ import annotations

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.scheduler.main import build_scan_schedules


def _xau_mtf() -> InstrumentConfig:
    return InstrumentConfig(
        symbol="XAUUSD",
        market=Market.METALS,
        provider="oanda",
        provider_symbol="XAU_USD",
        timeframes=[Timeframe.M5, Timeframe.M15, Timeframe.H4],
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
        entry_timeframes=[Timeframe.M5, Timeframe.M15],
        anchor_timeframe=Timeframe.H4,
    )


def test_one_entry_per_timeframe() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    assert len(schedules) == 3  # M5, M15, H4


def test_m5_runs_every_five_minutes() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    m5 = next(cron for _s, tf, cron in schedules if tf == "5m")
    assert m5["minute"] == "*/5"
    assert "second" in m5


def test_m15_runs_every_fifteen_minutes() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    m15 = next(cron for _s, tf, cron in schedules if tf == "15m")
    assert m15["minute"] == "*/15"


def test_h4_anchor_keeps_six_hour_grid() -> None:
    schedules = build_scan_schedules([_xau_mtf()])
    h4 = next(cron for _s, tf, cron in schedules if tf == "4h")
    assert h4["hour"] == "0,4,8,12,16,20"
