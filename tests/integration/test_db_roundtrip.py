"""P0-T4 AC: alembic upgrade head works + candle insert/select roundtrip.

Auto-skips when the dev stack is not running (`docker compose up -d` first).
"""

import os
import socket
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, text
from sqlalchemy.engine import make_url

from rtrade.core.constants import Timeframe
from rtrade.core.errors import DataValidationError
from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.models import Candle, Instrument
from rtrade.persistence.repositories import CandleRepo, CandleRow, InstrumentRepo

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


async def test_candles_is_hypertable(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = 'candles'"
                )
            )
            assert result.scalar_one() == 1
    finally:
        await engine.dispose()


async def test_candle_upsert_roundtrip_idempotent(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    factory = create_session_factory(engine)
    symbol = f"TST{uuid.uuid4().hex[:8].upper()}"
    try:
        async with factory() as session:
            inst = await InstrumentRepo(session).get_or_create(
                symbol=symbol,
                market="crypto",
                provider="test",
                provider_symbol="TST/USD",
                pip_size=Decimal("0.01"),
            )
            repo = CandleRepo(session)
            base = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
            rows = [
                CandleRow(
                    instrument_id=inst.id,
                    timeframe="1h",
                    ts=base.replace(hour=12 + i),
                    open=Decimal("100") + i,
                    high=Decimal("105") + i,
                    low=Decimal("99") + i,
                    close=Decimal("104") + i,
                    volume=Decimal("1000"),
                )
                for i in range(3)
            ]
            assert await repo.upsert_many(rows) == 3
            # Idempotency: same bars again, with one revised close.
            revised = CandleRow(
                instrument_id=inst.id,
                timeframe="1h",
                ts=base,
                open=Decimal("100"),
                high=Decimal("106"),
                low=Decimal("99"),
                close=Decimal("105.5"),
                volume=Decimal("1100"),
            )
            await repo.upsert_many([revised, *rows[1:]])
            await session.commit()

        async with factory() as session:
            repo = CandleRepo(session)
            fetched = await repo.get_range(inst.id, Timeframe.H1, base, base.replace(hour=18))
            assert len(fetched) == 3  # upsert, not duplicate
            assert fetched[0].close == Decimal("105.5")  # revision applied
            latest = await repo.latest(inst.id, Timeframe.H1)
            assert latest is not None
            assert latest.ts == base.replace(hour=14)
    finally:
        # Cleanup so reruns stay deterministic.
        async with factory() as session:
            inst_row = await InstrumentRepo(session).get_by_symbol(symbol)
            if inst_row is not None:
                await session.execute(delete(Candle).where(Candle.instrument_id == inst_row.id))
                await session.execute(delete(Instrument).where(Instrument.id == inst_row.id))
                await session.commit()
        await engine.dispose()


async def test_invalid_candle_rejected_before_db(migrated_db: str) -> None:
    with pytest.raises(DataValidationError, match="high < open/close"):
        CandleRow(
            instrument_id=1,
            timeframe="1h",
            ts=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
            open=Decimal("100"),
            high=Decimal("99"),  # invalid: high < open
            low=Decimal("98"),
            close=Decimal("99.5"),
        )
    with pytest.raises(DataValidationError, match="naive"):
        CandleRow(
            instrument_id=1,
            timeframe="1h",
            ts=datetime(2026, 6, 11, 12, 0),  # noqa: DTZ001 — intentional naive
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100.5"),
        )
