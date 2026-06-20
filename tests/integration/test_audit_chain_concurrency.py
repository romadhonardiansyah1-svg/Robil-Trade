"""D1: concurrent audit-chain appends must serialize into a single linear chain.

True-concurrency integration test: two `AuditRepo.add` calls run on separate
sessions/connections at the same time. The transaction-scoped advisory lock in
`AuditRepo.add` must serialize them so the two new rows form a linear chain
(second.prev_hash == first.row_hash) rather than forking off a shared parent.

Requires the docker compose dev DB; self-skips when no DB is reachable.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url

from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.models import SignalAudit
from rtrade.persistence.repositories import AuditRepo

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
async def test_concurrent_appends_form_linear_chain() -> None:
    url = _db_url()
    if not url:
        pytest.skip("no DATABASE_URL — live audit-chain concurrency test skipped")
    parsed = make_url(url)
    host, port = parsed.host or "localhost", parsed.port or 5432
    if not _tcp_reachable(host, port):
        pytest.skip(f"dev database not reachable at {host}:{port} — run `docker compose up -d`")

    engine = _get_engine(url)
    session_factory = create_session_factory(engine)

    marker = f"d1-concurrency-{uuid.uuid4().hex}"

    async def append_one(idx: int) -> None:
        # Each task uses its OWN session/connection — a true concurrent writer.
        async with session_factory() as session:
            await AuditRepo(session).add(
                stage="candidate",
                ok=True,
                detail={"marker": marker, "idx": idx},
                signal_id=f"{marker}-{idx}",
            )
            await session.commit()

    try:
        # Fire both writers concurrently against the same chain head.
        await asyncio.gather(append_one(0), append_one(1))

        async with session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(SignalAudit)
                        .where(SignalAudit.signal_id.in_([f"{marker}-0", f"{marker}-1"]))
                        .order_by(SignalAudit.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        assert len(rows) == 2, "expected exactly two appended audit rows"
        first, second = rows
        first_chain = first.detail["_chain"]
        second_chain = second.detail["_chain"]

        # No fork: the two rows must NOT chain off the same parent hash.
        assert first_chain["prev_hash"] != second_chain["prev_hash"], (
            "chain forked — both rows chained off the same parent (advisory lock missing)"
        )
        # Linear: the later row chains directly off the earlier row.
        assert second_chain["prev_hash"] == first_chain["row_hash"], (
            "chain is not linear — second row does not chain off the first"
        )
    finally:
        # Leave no test rows behind.
        async with session_factory() as session:
            for row in (
                (
                    await session.execute(
                        select(SignalAudit).where(
                            SignalAudit.signal_id.in_([f"{marker}-0", f"{marker}-1"])
                        )
                    )
                )
                .scalars()
                .all()
            ):
                await session.delete(row)
            await session.commit()
