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
