"""T10: Scheduler build from config tests."""

from rtrade.core.config import InstrumentConfig
from rtrade.core.constants import Market, Timeframe
from rtrade.scheduler.main import build_scan_schedules


def _make_inst(
    symbol: str,
    timeframes: list[Timeframe],
    *,
    provider: str = "twelvedata",
) -> InstrumentConfig:
    return InstrumentConfig(
        symbol=symbol,
        market=Market.METALS,
        provider=provider,
        provider_symbol=f"{symbol[:3]}/{symbol[3:]}",
        timeframes=timeframes,
        context_timeframe=Timeframe.D1,
        pip_size=0.01,
        quote_currency="USD",
    )


class TestBuildScanSchedules:
    def test_all_instruments_scheduled(self) -> None:
        instruments = [
            _make_inst("XAUUSD", [Timeframe.H1, Timeframe.H4]),
            _make_inst("EURUSD", [Timeframe.H1, Timeframe.H4]),
        ]
        result = build_scan_schedules(instruments)
        assert len(result) == 4  # 2 instruments × 2 TFs

    def test_seconds_staggered(self) -> None:
        instruments = [
            _make_inst("BTCUSDT", [Timeframe.H1], provider="ccxt_binance"),
            _make_inst("ETHUSDT", [Timeframe.H1], provider="ccxt_binance"),
        ]
        result = build_scan_schedules(instruments)
        sec_0 = result[0][2]["second"]
        sec_1 = result[1][2]["second"]
        assert sec_0 != sec_1

    def test_twelvedata_jobs_are_spread_by_minute(self) -> None:
        instruments = [
            _make_inst("XAUUSD", [Timeframe.H1]),
            _make_inst("EURUSD", [Timeframe.H1]),
            _make_inst("GBPUSD", [Timeframe.H1]),
            _make_inst("USDJPY", [Timeframe.H1]),
        ]
        result = build_scan_schedules(instruments)
        assert [r[2]["minute"] for r in result] == ["0", "10", "20", "30"]
        assert {r[2]["second"] for r in result} == {"30"}

    def test_4h_runs_on_4h_hours(self) -> None:
        instruments = [
            _make_inst("XAUUSD", [Timeframe.H1, Timeframe.H4]),
        ]
        result = build_scan_schedules(instruments)
        h4_entries = [r for r in result if r[1] == "4h"]
        assert len(h4_entries) == 1
        assert h4_entries[0][2]["hour"] == "0,4,8,12,16,20"
        assert h4_entries[0][2]["minute"] == "5"
