from __future__ import annotations

from datetime import date
import os
import socket

import pytest
from sqlalchemy.engine import make_url

from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.repositories import BacktestRunRepo

pytestmark = pytest.mark.integration


def _db_url() -> str | None:
    return os.environ.get("RTRADE_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_latest_for_returns_newest_run_per_strategy() -> None:
    url = _db_url()
    if not url:
        pytest.skip("no DATABASE_URL — live BacktestRunRepo test skipped")
    parsed = make_url(url)
    host, port = parsed.host or "localhost", parsed.port or 5432
    if not _tcp_reachable(host, port):
        pytest.skip(f"dev database not reachable at {host}:{port} — run `docker compose up -d`")
    engine = _get_engine(url)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        repo = BacktestRunRepo(session)
        await repo.add(
            strategy="s_probe",
            instrument="XAUUSD",
            window_start=date(2025, 1, 1),
            window_end=date(2025, 6, 1),
            is_oos=True,
            metrics={},
            gates={"all_passed": False},
            params={},
        )
        newest = await repo.add(
            strategy="s_probe",
            instrument="XAUUSD",
            window_start=date(2025, 6, 1),
            window_end=date(2026, 1, 1),
            is_oos=True,
            metrics={},
            gates={"all_passed": True},
            params={},
        )
        await session.flush()
        found = await repo.latest_for("s_probe", "XAUUSD")
        assert found is not None
        assert found.id == newest.id
        assert found.gates == {"all_passed": True}
        assert await repo.latest_for("does_not_exist") is None
        await session.rollback()  # leave no test rows behind
