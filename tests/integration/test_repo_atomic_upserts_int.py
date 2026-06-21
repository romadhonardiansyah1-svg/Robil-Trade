"""D5 (persistence): atomic upserts converge to a single row — DB-backed.

These exercise the real PostgreSQL ``INSERT ... ON CONFLICT`` paths end to end
and prove idempotency / convergence that the unit statement-shape tests cannot.
Auto-skips when the dev stack is not running (`docker compose up -d` first).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import os
from pathlib import Path
import socket
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.models import (
    CalendarSourceHealth,
    EconomicEvent,
    Instrument,
    StrategyState,
)
from rtrade.persistence.repositories import (
    CalendarSourceHealthRepo,
    EventRepo,
    InstrumentRepo,
    StrategyStateRepo,
)

pytestmark = pytest.mark.integration


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str, repo_root: Path) -> str:
    url = make_url(test_database_url)
    host, port = url.host or "localhost", url.port or 5432
    if not _tcp_reachable(host, port):
        pytest.skip(f"dev database not reachable at {host}:{port} — run `docker compose up -d`")
    env = {**os.environ, "DATABASE_URL": test_database_url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"
    return test_database_url


async def test_get_or_create_idempotent_and_concurrent_converge(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    factory = create_session_factory(engine)
    symbol = f"TST{uuid.uuid4().hex[:8].upper()}"
    try:
        # Sequential idempotency: two calls → same row, no IntegrityError.
        async with factory() as session:
            repo = InstrumentRepo(session)
            first = await repo.get_or_create(
                symbol=symbol,
                market="crypto",
                provider="test",
                provider_symbol="TST/USD",
                pip_size=Decimal("0.01"),
            )
            second = await repo.get_or_create(
                symbol=symbol,
                market="crypto",
                provider="test",
                provider_symbol="TST/USD",
                pip_size=Decimal("0.01"),
            )
            assert first.id == second.id
            await session.commit()

        # Concurrency: two writers on a fresh symbol via separate sessions/conns
        # must both succeed and converge on a single row.
        symbol2 = f"TST{uuid.uuid4().hex[:8].upper()}"

        async def _create() -> int:
            async with factory() as s:
                inst = await InstrumentRepo(s).get_or_create(
                    symbol=symbol2,
                    market="crypto",
                    provider="test",
                    provider_symbol="TST/USD",
                    pip_size=Decimal("0.01"),
                )
                await s.commit()
                return inst.id

        ids = await asyncio.gather(_create(), _create())
        assert ids[0] == ids[1]
    finally:
        async with factory() as session:
            for sym in (symbol, locals().get("symbol2")):
                if sym is None:
                    continue
                await session.execute(delete(Instrument).where(Instrument.symbol == sym))
            await session.commit()
        await engine.dispose()


async def test_set_state_idempotent_update(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    factory = create_session_factory(engine)
    strategy = f"strat_{uuid.uuid4().hex[:8]}"
    try:
        async with factory() as session:
            repo = StrategyStateRepo(session)
            await repo.set_state(strategy, enabled=False, reason="first")
            await session.commit()
        async with factory() as session:
            repo = StrategyStateRepo(session)
            await repo.set_state(strategy, enabled=True, reason=None)
            await session.commit()
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        StrategyState.__table__.select().where(StrategyState.strategy == strategy)
                    )
                )
                .mappings()
                .all()
            )
            assert len(rows) == 1  # one row, second call updated it
            assert rows[0]["enabled"] is True
            assert rows[0]["disabled_reason"] is None
    finally:
        async with factory() as session:
            await session.execute(delete(StrategyState).where(StrategyState.strategy == strategy))
            await session.commit()
        await engine.dispose()


async def test_calendar_source_health_upsert_idempotent(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    factory = create_session_factory(engine)
    source = f"src_{uuid.uuid4().hex[:8]}"
    ts = datetime(2026, 6, 20, 12, 0, tzinfo=UTC)
    try:
        async with factory() as session:
            repo = CalendarSourceHealthRepo(session)
            await repo.upsert(
                source,
                last_success=None,
                last_error="boom",
                consecutive_failures=3,
                last_attempt=ts,
            )
            await session.commit()
        async with factory() as session:
            repo = CalendarSourceHealthRepo(session)
            await repo.upsert(
                source,
                last_success=ts,
                last_error=None,
                consecutive_failures=0,
                last_attempt=ts,
            )
            await session.commit()
        async with factory() as session:
            rows = (await session.execute(CalendarSourceHealth.__table__.select())).mappings().all()
            matching = [r for r in rows if r["source"] == source]
            assert len(matching) == 1  # one row, second call updated it
            assert matching[0]["consecutive_failures"] == 0
            assert matching[0]["last_error"] is None
            assert matching[0]["last_success"] is not None
    finally:
        async with factory() as session:
            await session.execute(
                delete(CalendarSourceHealth).where(CalendarSourceHealth.source == source)
            )
            await session.commit()
        await engine.dispose()


async def test_event_upsert_many_idempotent(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    factory = create_session_factory(engine)
    event_id = f"evt_{uuid.uuid4().hex[:8]}"
    fetched = datetime(2026, 6, 20, 11, 0, tzinfo=UTC)

    def _event(impact: str) -> EconomicEvent:
        return EconomicEvent(
            id=event_id,
            event="Non-Farm Payrolls",
            currency="USD",
            impact=impact,
            event_time=datetime(2026, 6, 20, 12, 0, tzinfo=UTC),
            fetched_at=fetched,
        )

    try:
        async with factory() as session:
            assert await EventRepo(session).upsert_many([_event("low")]) == 1
            await session.commit()
        async with factory() as session:
            assert await EventRepo(session).upsert_many([_event("high")]) == 1
            await session.commit()
        async with factory() as session:
            rows = (await session.execute(EconomicEvent.__table__.select())).mappings().all()
            matching = [r for r in rows if r["id"] == event_id]
            assert len(matching) == 1  # upsert, not duplicate
            assert matching[0]["impact"] == "high"  # revision applied
    finally:
        async with factory() as session:
            await session.execute(delete(EconomicEvent).where(EconomicEvent.id == event_id))
            await session.commit()
        await engine.dispose()
