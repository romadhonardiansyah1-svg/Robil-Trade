from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from rtrade.core.errors import RateLimitExceeded
from rtrade.persistence.audit_chain import build_chain_entry
from rtrade.scheduler import jobs


@pytest.fixture(autouse=True)
def _reset_job_state() -> None:
    jobs._fail_counts.clear()
    jobs._last_alert_at.clear()


@pytest.mark.asyncio
async def test_scan_job_suppresses_rate_limit_telegram_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_scan(*args: Any, **kwargs: Any) -> None:
        raise RateLimitExceeded("TwelveData 429: rate limit hit")

    alerts: list[str] = []

    async def collect_alert(message: str) -> None:
        alerts.append(message)

    monkeypatch.setattr(jobs, "run_scan", fail_scan)
    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)

    for _ in range(4):
        await jobs.scan_job("USDJPY", "1h")

    assert alerts == []
    assert jobs._fail_counts["USDJPY:1h"] == 4


@pytest.mark.asyncio
async def test_scan_job_alerts_non_rate_limit_once_until_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_scan(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    alerts: list[str] = []

    async def collect_alert(message: str) -> None:
        alerts.append(message)

    monkeypatch.setattr(jobs, "run_scan", fail_scan)
    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)

    for _ in range(4):
        await jobs.scan_job("USDJPY", "1h")

    assert len(alerts) == 1
    assert "database unavailable" in alerts[0]


# ---------------------------------------------------------------------------
# D3: audit_chain_verify_job must integrity-check the LATEST rows, not the
# first 1000. Once the table exceeds 1000 rows the old ascending+limit window
# never re-examines recent rows.
# ---------------------------------------------------------------------------


class _AuditRow:
    """Minimal SignalAudit stand-in exposing the fields the job reads."""

    def __init__(self, stage: str, ok: bool, signal_id: str | None, detail: dict) -> None:
        self.stage = stage
        self.ok = ok
        self.signal_id = signal_id
        self.detail = detail


class _ScalarResult:
    def __init__(self, rows: list[_AuditRow]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[_AuditRow]:
        return self._rows


class _WindowSession:
    """Fake AsyncSession that honors ORDER BY direction + a 1000-row LIMIT so
    we can prove the job selects the LATEST window, not the earliest one.
    """

    def __init__(self, rows_asc: list[_AuditRow]) -> None:
        self.rows_asc = rows_asc
        self.executed_sql: list[str] = []

    async def __aenter__(self) -> _WindowSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, statement: object) -> _ScalarResult:
        sql = str(statement)
        self.executed_sql.append(sql)
        if "DESC" in sql:
            window = list(reversed(self.rows_asc[-1000:]))  # latest 1000, desc id
        else:
            window = self.rows_asc[:1000]  # earliest 1000, asc id (legacy bug)
        return _ScalarResult(window)


def _build_audit_rows(n: int) -> list[_AuditRow]:
    rows: list[_AuditRow] = []
    prev_hash = "genesis"
    for i in range(n):
        detail: dict = {"info": f"row_{i}"}
        chain = build_chain_entry(prev_hash, f"stage_{i}", True, f"sig_{i}", detail)
        detail["_chain"] = chain
        rows.append(_AuditRow(f"stage_{i}", True, f"sig_{i}", detail))
        prev_hash = chain["row_hash"]
    return rows


def _wire_audit_job(
    monkeypatch: pytest.MonkeyPatch, session: _WindowSession
) -> list[tuple[str, object]]:
    """Patch config/engine/session-factory so audit_chain_verify_job runs against
    the in-memory fake, and capture any failure alerts.
    """
    from rtrade.persistence import db as db_mod

    cfg = SimpleNamespace(secrets=SimpleNamespace(database_url="sqlite://"))
    monkeypatch.setattr(jobs.AppConfig, "load", staticmethod(lambda: cfg))
    monkeypatch.setattr(db_mod, "_get_engine", lambda url: object())
    monkeypatch.setattr(db_mod, "create_session_factory", lambda engine: lambda: session)

    alerts: list[tuple[str, object]] = []

    async def collect_alert(message: str, *, alert_type: object = None) -> None:
        alerts.append((message, alert_type))

    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)
    return alerts


@pytest.mark.asyncio
async def test_audit_verify_queries_latest_rows_descending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integrity SELECT must order by DESCENDING id (latest rows)."""
    session = _WindowSession(_build_audit_rows(1001))
    _wire_audit_job(monkeypatch, session)

    await jobs.audit_chain_verify_job()

    assert session.executed_sql, "job issued no SELECT"
    assert "DESC" in session.executed_sql[0], "must fetch latest rows by descending id"


@pytest.mark.asyncio
async def test_audit_verify_detects_break_in_latest_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tampered LATEST row (id 1001) must be detected. The legacy ascending
    window (rows 1..1000) would silently skip it.
    """
    rows = _build_audit_rows(1001)
    rows[-1].detail["info"] = "TAMPERED"  # corrupt the most recent row
    session = _WindowSession(rows)
    alerts = _wire_audit_job(monkeypatch, session)

    await jobs.audit_chain_verify_job()

    assert len(alerts) == 1, "tampering in the latest rows must raise an alert"
    assert "AUDIT CHAIN BROKEN" in alerts[0][0]


@pytest.mark.asyncio
async def test_audit_verify_no_false_alarm_on_valid_latest_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fully valid 1001-row chain must NOT false-alarm: the latest-1000 window
    starts mid-chain, so the job must anchor on the window edge.
    """
    session = _WindowSession(_build_audit_rows(1001))
    alerts = _wire_audit_job(monkeypatch, session)

    await jobs.audit_chain_verify_job()

    assert alerts == [], "valid recent window must not be reported as broken"


# ---------------------------------------------------------------------------
# E2: hmm_train_job must NOT run CPU-bound training on the event loop while
# holding a DB session. It must release the session, then offload to an executor.
# ---------------------------------------------------------------------------


class _TrackingSession:
    """Fake session that records when it is closed (context exit)."""

    def __init__(self, events: list[str], row: object, candles: list[object]) -> None:
        self.events = events
        self._row = row
        self._candles = candles

    async def __aenter__(self) -> _TrackingSession:
        self.events.append("session_open")
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.events.append("session_closed")
        return False


class _FakeInstrumentRepo:
    _row: object = None

    def __init__(self, session: _TrackingSession) -> None:
        self._session = session

    async def get_by_symbol(self, symbol: str) -> object:
        return self._session._row


class _FakeCandleRepo:
    def __init__(self, session: _TrackingSession) -> None:
        self._session = session

    async def latest_n(self, instrument_id: object, tf: object, n: int) -> list[object]:
        return self._session._candles


def _make_candle() -> object:
    from datetime import UTC, datetime

    return SimpleNamespace(
        ts=datetime(2025, 1, 1, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
    )


@pytest.mark.asyncio
async def test_hmm_train_offloads_to_executor_after_releasing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CPU-bound training runs via run_in_executor (a worker thread, NOT the
    event-loop thread) and only AFTER the DB session is released.
    """
    from rtrade.indicators import engine as engine_mod
    from rtrade.ml import model_io as model_io_mod
    from rtrade.persistence import db as db_mod
    from rtrade.persistence import repositories as repo_mod
    from rtrade.regime import hmm as hmm_mod

    events: list[str] = []
    train_thread: dict[str, int] = {}

    row = SimpleNamespace(id=1)
    candles = [_make_candle() for _ in range(600)]
    session = _TrackingSession(events, row, candles)

    cfg = SimpleNamespace(
        instruments=[SimpleNamespace(symbol="USDJPY")],
        secrets=SimpleNamespace(database_url="sqlite://", model_hmac_key="k"),
    )
    monkeypatch.setattr(jobs.AppConfig, "load", staticmethod(lambda: cfg))
    monkeypatch.setattr(db_mod, "_get_engine", lambda url: object())
    monkeypatch.setattr(db_mod, "create_session_factory", lambda engine: lambda: session)
    monkeypatch.setattr(repo_mod, "InstrumentRepo", _FakeInstrumentRepo)
    monkeypatch.setattr(repo_mod, "CandleRepo", _FakeCandleRepo)

    # Guard: ensure the real (heavy) training path is never exercised on the loop.
    def _boom(*a: object, **k: object) -> object:
        raise AssertionError("CPU-bound training ran inline on the event loop")

    monkeypatch.setattr(engine_mod, "compute", _boom)
    monkeypatch.setattr(hmm_mod, "HMMRegimeDetector", _boom)
    monkeypatch.setattr(model_io_mod, "save_model", _boom)

    def fake_blocking(df: object, symbol: str, hmac_key: object, out_dir: object) -> None:
        train_thread["id"] = threading.get_ident()
        events.append(f"train:{symbol}")

    monkeypatch.setattr(jobs, "_train_hmm_blocking", fake_blocking, raising=False)

    await jobs.hmm_train_job()

    assert "train:USDJPY" in events, "blocking trainer was never invoked"
    # Session must be released BEFORE the CPU-bound work runs.
    assert events.index("session_closed") < events.index("train:USDJPY")
    # Work must be offloaded to a worker thread, not the event-loop thread.
    assert train_thread["id"] != threading.get_ident()
