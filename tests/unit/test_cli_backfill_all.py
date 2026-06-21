"""rtrade.cli.backfill `--all` mode: data-driven, fail-soft backfill.

`_run` is fully stubbed (no DB/network). `AppConfig.load` is monkeypatched to
return a tiny fake cfg with two instruments, each carrying real `Timeframe`
enum members so `tf.value` resolves exactly like production.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from rtrade.core.constants import Timeframe


@dataclass
class _FakeInstrument:
    symbol: str
    timeframes: list[Timeframe] = field(default_factory=list)


@dataclass
class _FakeCfg:
    instruments: list[_FakeInstrument]


def _patch_cfg(monkeypatch: pytest.MonkeyPatch, cfg: _FakeCfg) -> None:
    import rtrade.cli.backfill as backfill

    monkeypatch.setattr(
        backfill.AppConfig,
        "load",
        classmethod(lambda cls, **kw: cfg),
    )


@pytest.mark.asyncio
async def test_run_all_iterates_all_instruments_timeframes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rtrade.cli.backfill as backfill

    cfg = _FakeCfg(
        instruments=[
            _FakeInstrument("XAUUSD", [Timeframe.H1, Timeframe.H4]),
            _FakeInstrument("BTCUSDT", [Timeframe.M15, Timeframe.D1]),
        ]
    )
    _patch_cfg(monkeypatch, cfg)

    calls: list[tuple[str, str]] = []

    async def fake_run(symbol: str, timeframe: str, days: int, config_dir: str) -> None:
        calls.append((symbol, timeframe))

    monkeypatch.setattr(backfill, "_run", fake_run)

    result = await backfill._run_all(days=10, config_dir="config")

    assert calls == [
        ("XAUUSD", "1h"),
        ("XAUUSD", "4h"),
        ("BTCUSDT", "15m"),
        ("BTCUSDT", "1d"),
    ]
    assert [status for _, _, status in result] == ["ok", "ok", "ok", "ok"]
    assert {(s, tf) for s, tf, _ in result} == set(calls)


@pytest.mark.asyncio
async def test_run_all_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    import rtrade.cli.backfill as backfill

    cfg = _FakeCfg(
        instruments=[
            _FakeInstrument("XAUUSD", [Timeframe.H1, Timeframe.H4]),
            _FakeInstrument("BTCUSDT", [Timeframe.M15]),
        ]
    )
    _patch_cfg(monkeypatch, cfg)

    calls: list[tuple[str, str]] = []

    async def fake_run(symbol: str, timeframe: str, days: int, config_dir: str) -> None:
        calls.append((symbol, timeframe))
        if symbol == "XAUUSD" and timeframe == "4h":
            raise RuntimeError("boom on this one")

    monkeypatch.setattr(backfill, "_run", fake_run)

    result = await backfill._run_all(days=5, config_dir="config")

    # All three were attempted despite the failure in the middle.
    assert calls == [("XAUUSD", "1h"), ("XAUUSD", "4h"), ("BTCUSDT", "15m")]

    status_by_key = {(s, tf): status for s, tf, status in result}
    assert status_by_key[("XAUUSD", "1h")] == "ok"
    assert status_by_key[("BTCUSDT", "15m")] == "ok"
    assert status_by_key[("XAUUSD", "4h")].startswith("FAILED")


def test_main_requires_symbol_without_all(monkeypatch: pytest.MonkeyPatch) -> None:
    import rtrade.cli.backfill as backfill

    monkeypatch.setattr("sys.argv", ["backfill"])
    with pytest.raises(SystemExit):
        backfill.main()
