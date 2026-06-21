"""D5 (persistence): atomic upserts replace TOCTOU/N+1 writes.

Unit-level statement-shape assertions (no live DB). We spy on the session's
``execute`` calls and assert the repositories emit a SINGLE PostgreSQL
``INSERT ... ON CONFLICT`` statement instead of per-row ``merge`` /
get-then-modify round trips.

Concurrency convergence (two writers, one row) is exercised by the
DB-backed tests in ``tests/integration`` (``integration`` marker).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.dialects.postgresql.dml import (
    Insert as PgInsert,
)
from sqlalchemy.dialects.postgresql.dml import (
    OnConflictDoNothing,
    OnConflictDoUpdate,
)

from rtrade.persistence.models import EconomicEvent, Instrument
from rtrade.persistence.repositories import (
    CalendarSourceHealthRepo,
    EventRepo,
    InstrumentRepo,
    StrategyStateRepo,
)


class _FakeResult:
    def __init__(self, scalar: Any = None) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _RecordingSession:
    """Minimal async session double that records execute()/merge()/add() calls."""

    def __init__(self, select_scalar: Any = None) -> None:
        self.executed: list[Any] = []
        self.merged: list[Any] = []
        self.added: list[Any] = []
        self.got: list[Any] = []
        self._select_scalar = select_scalar

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _FakeResult:
        self.executed.append(statement)
        return _FakeResult(self._select_scalar)

    async def merge(self, obj: Any) -> Any:
        self.merged.append(obj)
        return obj

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def get(self, *args: Any, **kwargs: Any) -> Any:
        self.got.append((args, kwargs))
        return None

    async def flush(self) -> None:
        return None


def _inserts(session: _RecordingSession) -> list[PgInsert]:
    return [s for s in session.executed if isinstance(s, PgInsert)]


def _event(event_id: str) -> EconomicEvent:
    return EconomicEvent(
        id=event_id,
        event="Non-Farm Payrolls",
        currency="USD",
        impact="high",
        event_time=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        fetched_at=datetime(2026, 6, 20, 11, 0, tzinfo=UTC),
    )


async def test_event_upsert_many_issues_single_on_conflict_insert() -> None:
    session = _RecordingSession()
    repo = EventRepo(session)  # type: ignore[arg-type]

    events = [_event("a"), _event("b"), _event("c")]
    count = await repo.upsert_many(events)

    assert count == 3  # return contract preserved
    assert session.merged == []  # N+1 merge loop gone
    inserts = _inserts(session)
    assert len(inserts) == 1  # one batched statement, not N
    clause = inserts[0]._post_values_clause
    assert isinstance(clause, OnConflictDoUpdate)


async def test_event_upsert_many_empty_is_noop() -> None:
    session = _RecordingSession()
    repo = EventRepo(session)  # type: ignore[arg-type]

    assert await repo.upsert_many([]) == 0
    assert session.executed == []
    assert session.merged == []


async def test_set_state_issues_on_conflict_do_update() -> None:
    session = _RecordingSession()
    repo = StrategyStateRepo(session)  # type: ignore[arg-type]

    await repo.set_state("s3_mtf_scalper", enabled=False, reason="drawdown")

    inserts = _inserts(session)
    assert len(inserts) == 1
    assert isinstance(inserts[0]._post_values_clause, OnConflictDoUpdate)
    # Pure upsert: no get-then-modify probe drives the write.
    assert session.got == []


async def test_calendar_source_health_upsert_issues_on_conflict_do_update() -> None:
    session = _RecordingSession()
    repo = CalendarSourceHealthRepo(session)  # type: ignore[arg-type]

    await repo.upsert(
        "forexfactory",
        last_success=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
        last_error=None,
        consecutive_failures=0,
        last_attempt=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
    )

    inserts = _inserts(session)
    assert len(inserts) == 1
    assert isinstance(inserts[0]._post_values_clause, OnConflictDoUpdate)
    assert session.got == []


async def test_get_or_create_uses_on_conflict_do_nothing_then_selects() -> None:
    existing = Instrument(
        id=7,
        symbol="XAUUSD",
        market="metals",
        provider="test",
        provider_symbol="XAU/USD",
        pip_size=Decimal("0.01"),
        config={},
    )
    session = _RecordingSession(select_scalar=existing)
    repo = InstrumentRepo(session)  # type: ignore[arg-type]

    result = await repo.get_or_create(
        symbol="XAUUSD",
        market="metals",
        provider="test",
        provider_symbol="XAU/USD",
        pip_size=Decimal("0.01"),
    )

    # Race-safe: a single INSERT ... ON CONFLICT DO NOTHING, then a SELECT.
    inserts = _inserts(session)
    assert len(inserts) == 1
    assert isinstance(inserts[0]._post_values_clause, OnConflictDoNothing)
    # Returned-object contract preserved (callers rely on .id).
    assert result is existing
    assert result.id == 7
    assert session.added == []  # no ORM unit-of-work add path
