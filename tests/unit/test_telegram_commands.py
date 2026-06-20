"""Unit tests for DB-backed Telegram commands (audit E1 / P3-1).

Deterministic, no network, no live DB:
- HealthChecker is injected as a fake.
- The DB session factory is a fake async context manager.
- Repository classes are monkeypatched with in-memory fakes.
- aiogram Message is a tiny stub with an async ``.answer`` capturing replies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from rtrade.delivery import telegram_bot as tb
from rtrade.delivery.telegram_bot import (
    CalibrationStats,
    TelegramDelivery,
    format_calibration_text,
    format_signals_text,
    format_status_text,
)
from rtrade.monitoring.healthcheck import CheckResult, HealthStatus, SystemHealth

BOT_TOKEN = "123456:test-token"
CHAT_ID = "999"


# --- test doubles --------------------------------------------------------------


class _FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class _FakeMessage:
    def __init__(self, text: str, chat_id: int) -> None:
        self.text = text
        self.chat = _FakeChat(chat_id)
        self.replies: list[str] = []

    async def answer(self, text: str, **_: Any) -> None:
        self.replies.append(text)


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class _FakeSessionFactory:
    """Callable returning an async-context-manager that yields a fake session."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSessionFactory:
        return self

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _FakeHealthChecker:
    def __init__(self, health: SystemHealth) -> None:
        self._health = health

    async def run_all(self) -> SystemHealth:
        return self._health


def _make_signal(
    *,
    instrument_id: int = 1,
    action: str = "BUY",
    status: str = "PUBLISHED",
    strategy: str = "s1_trend_pullback",
    entry: str | None = "1234.5",
    sl: str | None = "1230.0",
    tp: str | None = "1245.0",
    confidence: str | None = "0.72",
) -> SimpleNamespace:
    return SimpleNamespace(
        instrument_id=instrument_id,
        action=action,
        status=status,
        strategy=strategy,
        entry_limit=Decimal(entry) if entry is not None else None,
        stop_loss=Decimal(sl) if sl is not None else None,
        take_profit=Decimal(tp) if tp is not None else None,
        confidence=Decimal(confidence) if confidence is not None else None,
        bar_ts=datetime(2024, 1, 2, 12, 0, tzinfo=UTC),
    )


def _make_bot(**kwargs: Any) -> TelegramDelivery:
    return TelegramDelivery(BOT_TOKEN, CHAT_ID, **kwargs)


# --- pure formatting helpers ---------------------------------------------------


def test_format_status_reflects_components() -> None:
    health = SystemHealth(
        status=HealthStatus.DEGRADED,
        checks=[
            CheckResult(name="database", status=HealthStatus.HEALTHY, message="connected"),
            CheckResult(name="redis", status=HealthStatus.DEGRADED, message="slow"),
        ],
    )
    text = format_status_text(health)
    assert "DEGRADED" in text
    assert "database: HEALTHY" in text
    assert "redis: DEGRADED" in text


def test_format_signals_formats_seeded() -> None:
    signals = [_make_signal(), _make_signal(action="SELL", status="TP_HIT", confidence=None)]
    text = format_signals_text(signals, {1: "XAUUSD"})
    assert "2 sinyal terakhir" in text
    assert "XAUUSD BUY [PUBLISHED] s1_trend_pullback" in text
    assert "entry=1234.5 SL=1230.0 TP=1245.0 conf=0.72" in text
    assert "XAUUSD SELL [TP_HIT]" in text
    assert "conf=-" in text  # None confidence renders as a dash


def test_format_signals_empty_is_honest() -> None:
    text = format_signals_text([], {})
    assert "Belum ada sinyal" in text


def test_format_calibration_computes_metrics() -> None:
    stats = CalibrationStats(
        wins=6, losses=4, outcomes=[1.0, 1.0, -1.0, 2.0], published=10, abstained=10
    )
    text = format_calibration_text(stats)
    assert "Win Rate: 60.0% (6W / 4L)" in text
    assert "Expectancy: +0.750R" in text
    assert "Abstain Rate: 50.0% (10/20)" in text


def test_format_calibration_insufficient_data() -> None:
    stats = CalibrationStats(wins=0, losses=0, outcomes=[], published=0, abstained=0)
    text = format_calibration_text(stats)
    assert "Belum cukup data" in text


# --- handlers (DB-backed) ------------------------------------------------------


@pytest.mark.asyncio
async def test_status_handler_reflects_mocked_health() -> None:
    health = SystemHealth(
        status=HealthStatus.HEALTHY,
        checks=[CheckResult(name="database", status=HealthStatus.HEALTHY, message="connected")],
    )
    bot = _make_bot(health_checker=_FakeHealthChecker(health))
    msg = _FakeMessage("/status", int(CHAT_ID))
    await bot._handle_status(msg)  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    assert "HEALTHY" in msg.replies[0]
    assert "database: HEALTHY" in msg.replies[0]


@pytest.mark.asyncio
async def test_signals_handler_formats_seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    seeded = [_make_signal()]

    class _SignalRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def recent(self, limit: int = 5) -> list[Any]:
            return seeded[:limit]

    class _InstrumentRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def get_by_id(self, _id: int) -> Any:
            return SimpleNamespace(symbol="XAUUSD")

    monkeypatch.setattr(tb, "SignalRepo", _SignalRepo)
    monkeypatch.setattr(tb, "InstrumentRepo", _InstrumentRepo)

    bot = _make_bot(session_factory=_FakeSessionFactory(_FakeSession()))
    msg = _FakeMessage("/signals", int(CHAT_ID))
    await bot._handle_signals(msg)  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    assert "XAUUSD BUY [PUBLISHED]" in msg.replies[0]


@pytest.mark.asyncio
async def test_signals_handler_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SignalRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def recent(self, limit: int = 5) -> list[Any]:
            return []

    monkeypatch.setattr(tb, "SignalRepo", _SignalRepo)
    bot = _make_bot(session_factory=_FakeSessionFactory(_FakeSession()))
    msg = _FakeMessage("/signals", int(CHAT_ID))
    await bot._handle_signals(msg)  # type: ignore[arg-type]
    assert "Belum ada sinyal" in msg.replies[0]


@pytest.mark.asyncio
async def test_enable_strategy_known_name_writes_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, str | None]] = []

    class _StrategyStateRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def set_state(
            self, strategy: str, *, enabled: bool, reason: str | None = None
        ) -> None:
            calls.append((strategy, enabled, reason))

    monkeypatch.setattr(tb, "StrategyStateRepo", _StrategyStateRepo)
    session = _FakeSession()
    bot = _make_bot(session_factory=_FakeSessionFactory(session))
    msg = _FakeMessage("/enable_strategy s1_trend_pullback", int(CHAT_ID))
    await bot._handle_enable_strategy(msg)  # type: ignore[arg-type]

    assert calls == [("s1_trend_pullback", True, "manual re-enable via telegram")]
    assert session.committed is True
    assert "diaktifkan kembali" in msg.replies[0]


@pytest.mark.asyncio
async def test_enable_strategy_unknown_name_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    class _StrategyStateRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def set_state(self, *_: Any, **__: Any) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr(tb, "StrategyStateRepo", _StrategyStateRepo)
    bot = _make_bot(session_factory=_FakeSessionFactory(_FakeSession()))
    msg = _FakeMessage("/enable_strategy not_a_strategy", int(CHAT_ID))
    await bot._handle_enable_strategy(msg)  # type: ignore[arg-type]

    assert called is False
    assert "tidak dikenal" in msg.replies[0]


@pytest.mark.asyncio
async def test_calibration_handler_computes(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SignalRepo:
        def __init__(self, _session: Any) -> None:
            pass

        async def resolved_outcomes_since(self, _start: datetime) -> list[tuple[str, float]]:
            return [("TP_HIT", 1.5), ("SL_HIT", -1.0), ("TP_HIT", 1.5)]

        async def status_counts_since(self, _start: datetime) -> dict[str, int]:
            return {"PUBLISHED": 8, "ABSTAINED": 2}

    monkeypatch.setattr(tb, "SignalRepo", _SignalRepo)
    bot = _make_bot(session_factory=_FakeSessionFactory(_FakeSession()))
    msg = _FakeMessage("/calibration", int(CHAT_ID))
    await bot._handle_calibration(msg)  # type: ignore[arg-type]
    reply = msg.replies[0]
    assert "Win Rate: 66.7% (2W / 1L)" in reply
    assert "Abstain Rate: 20.0% (2/10)" in reply


@pytest.mark.asyncio
async def test_unauthorized_chat_is_ignored() -> None:
    bot = _make_bot(session_factory=_FakeSessionFactory(_FakeSession()))
    msg = _FakeMessage("/status", 12345)  # not the whitelisted CHAT_ID
    await bot._handle_status(msg)  # type: ignore[arg-type]
    await bot._handle_signals(msg)  # type: ignore[arg-type]
    await bot._handle_enable_strategy(msg)  # type: ignore[arg-type]
    assert msg.replies == []


@pytest.mark.asyncio
async def test_mute_still_works() -> None:
    bot = _make_bot()
    msg = _FakeMessage("/mute 2h", int(CHAT_ID))
    await bot._handle_mute(msg)  # type: ignore[arg-type]
    assert "di-mute selama 2 jam" in msg.replies[0]
    assert bot.is_muted is True
