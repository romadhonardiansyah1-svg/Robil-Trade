"""Tests for tamper-evident audit hash chain (S9)."""

from __future__ import annotations

from rtrade.persistence.audit_chain import build_chain_entry, verify_chain


class TestAuditChain:
    def test_chain_of_3_consistent(self) -> None:
        """3 entries built sequentially verify as consistent."""
        entries = []
        prev_hash = "genesis"
        for i in range(3):
            detail: dict = {"info": f"test_{i}"}
            chain = build_chain_entry(prev_hash, f"stage_{i}", True, f"sig_{i}", detail)
            detail["_chain"] = chain
            entries.append(
                {"stage": f"stage_{i}", "ok": True, "signal_id": f"sig_{i}", "detail": detail}
            )
            prev_hash = chain["row_hash"]

        ok, count = verify_chain(entries)
        assert ok is True
        assert count == 3

    def test_tampered_detail_detected(self) -> None:
        """Modifying detail after chain creation → verify detects break."""
        entries = []
        prev_hash = "genesis"
        for i in range(3):
            detail: dict = {"info": f"original_{i}"}
            chain = build_chain_entry(prev_hash, f"stage_{i}", True, f"sig_{i}", detail)
            detail["_chain"] = chain
            entries.append(
                {"stage": f"stage_{i}", "ok": True, "signal_id": f"sig_{i}", "detail": detail}
            )
            prev_hash = chain["row_hash"]

        # Tamper with entry 1
        entries[1]["detail"]["info"] = "TAMPERED"
        ok, idx = verify_chain(entries)
        assert ok is False
        assert idx == 1  # break at index 1

    def test_tampered_stage_detected(self) -> None:
        """Modifying stage → verify detects break."""
        entries = []
        prev_hash = "genesis"
        for i in range(2):
            detail: dict = {"x": i}
            chain = build_chain_entry(prev_hash, f"s{i}", True, None, detail)
            detail["_chain"] = chain
            entries.append({"stage": f"s{i}", "ok": True, "signal_id": None, "detail": detail})
            prev_hash = chain["row_hash"]

        entries[0]["stage"] = "TAMPERED"
        ok, idx = verify_chain(entries)
        assert ok is False
        assert idx == 0

    def test_empty_chain_ok(self) -> None:
        ok, count = verify_chain([])
        assert ok is True
        assert count == 0

    def test_single_entry_ok(self) -> None:
        detail: dict = {"msg": "hello"}
        chain = build_chain_entry("genesis", "init", True, None, detail)
        detail["_chain"] = chain
        entries = [{"stage": "init", "ok": True, "signal_id": None, "detail": detail}]
        ok, count = verify_chain(entries)
        assert ok is True
        assert count == 1


class _FakeResult:
    """Minimal SQLAlchemy Result stand-in for the latest-row SELECT."""

    def scalar_one_or_none(self) -> None:
        return None


class _RecordingSession:
    """Fake AsyncSession that records the order of execute() statements.

    Lets us prove (without a live DB) that AuditRepo.add takes the advisory
    lock BEFORE it reads the latest audit row — i.e. the serialization is
    wired correctly (D1).
    """

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.added: list[object] = []

    async def execute(self, statement: object, params: object = None) -> _FakeResult:
        self.executed.append(str(statement))
        return _FakeResult()

    def add(self, obj: object) -> None:
        self.added.append(obj)


class TestAuditRepoSerialization:
    """D1: chain appends must be serialized with a transaction-scoped lock."""

    async def test_advisory_lock_issued_before_latest_select(self) -> None:
        from rtrade.persistence.repositories import AuditRepo

        session = _RecordingSession()
        repo = AuditRepo(session)  # type: ignore[arg-type]

        await repo.add(stage="candidate", ok=True, detail={"x": 1}, signal_id="sig1")

        # First statement must be the advisory lock.
        assert session.executed, "AuditRepo.add issued no statements"
        assert "pg_advisory_xact_lock" in session.executed[0]

        # The latest-row SELECT must come strictly after the lock.
        lock_idx = next(i for i, s in enumerate(session.executed) if "pg_advisory_xact_lock" in s)
        select_idx = next(i for i, s in enumerate(session.executed) if "signal_audits" in s)
        assert lock_idx < select_idx, (
            "advisory lock must precede the latest-row SELECT (serialize before read)"
        )

    async def test_add_does_not_mutate_caller_detail(self) -> None:
        from rtrade.persistence.repositories import AuditRepo

        session = _RecordingSession()
        repo = AuditRepo(session)  # type: ignore[arg-type]

        caller_detail: dict = {"x": 1}
        await repo.add(stage="candidate", ok=True, detail=caller_detail, signal_id="sig1")

        # Caller's dict must be left untouched (no injected _chain key).
        assert caller_detail == {"x": 1}
        assert "_chain" not in caller_detail


class TestVerifyChainAnchor:
    """D3: a 'recent rows' window does not start at genesis. verify_chain must
    support anchoring on the first window entry's own prev_hash so the latest
    rows can be integrity-checked without false-alarming on the window edge.
    """

    @staticmethod
    def _build(n: int) -> list[dict]:
        entries: list[dict] = []
        prev_hash = "genesis"
        for i in range(n):
            detail: dict = {"info": f"row_{i}"}
            chain = build_chain_entry(prev_hash, f"stage_{i}", True, f"sig_{i}", detail)
            detail["_chain"] = chain
            entries.append(
                {"stage": f"stage_{i}", "ok": True, "signal_id": f"sig_{i}", "detail": detail}
            )
            prev_hash = chain["row_hash"]
        return entries

    def test_window_not_starting_at_genesis_fails_without_anchor(self) -> None:
        """Default behavior (genesis anchor) rejects a mid-chain window."""
        window = self._build(5)[2:]
        ok, idx = verify_chain(window)
        assert ok is False
        assert idx == 0  # first window row's prev_hash != "genesis"

    def test_window_not_starting_at_genesis_ok_with_anchor(self) -> None:
        """anchor_first=True accepts a valid mid-chain window."""
        window = self._build(5)[2:]
        ok, count = verify_chain(window, anchor_first=True)
        assert ok is True
        assert count == len(window)

    def test_anchor_first_still_detects_tamper_in_window(self) -> None:
        """Anchoring the edge must NOT weaken detection of inner tampering."""
        window = self._build(5)[2:]
        window[1]["detail"]["info"] = "TAMPERED"
        ok, idx = verify_chain(window, anchor_first=True)
        assert ok is False
        assert idx == 1

    def test_anchor_first_detects_tamper_in_first_window_row(self) -> None:
        """Even the anchored first row's own content is still hash-verified."""
        window = self._build(5)[2:]
        window[0]["stage"] = "TAMPERED"
        ok, idx = verify_chain(window, anchor_first=True)
        assert ok is False
        assert idx == 0
