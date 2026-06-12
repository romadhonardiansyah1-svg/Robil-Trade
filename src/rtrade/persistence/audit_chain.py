"""Tamper-evident hash chain for audit log (S9).

Pure functions — no DB dependency; usable in both repo and offline verification.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_json(data: dict[str, Any]) -> str:
    """Deterministic JSON serialization for hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(
    prev_hash: str,
    stage: str,
    ok: bool,
    signal_id: str | None,
    detail: dict[str, Any],
) -> str:
    """Compute SHA-256 hash for a single audit row."""
    # Remove existing chain data from detail before hashing
    detail_clean = {k: v for k, v in detail.items() if k != "_chain"}
    payload = _canonical_json(
        {
            "prev_hash": prev_hash,
            "stage": stage,
            "ok": ok,
            "signal_id": signal_id,
            "detail": detail_clean,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def build_chain_entry(
    prev_hash: str,
    stage: str,
    ok: bool,
    signal_id: str | None,
    detail: dict[str, Any],
) -> dict[str, str]:
    """Build the _chain entry to embed in detail JSONB."""
    row_hash = compute_row_hash(prev_hash, stage, ok, signal_id, detail)
    return {"prev_hash": prev_hash, "row_hash": row_hash}


def verify_chain(entries: list[dict[str, Any]]) -> tuple[bool, int]:
    """Verify a list of audit entries (dicts with stage, ok, signal_id, detail).

    Returns (True, count) if all hashes are consistent;
    (False, first_broken_index) if a chain break is found.
    """
    prev_hash = "genesis"
    for idx, entry in enumerate(entries):
        detail = entry.get("detail", {})
        chain = detail.get("_chain", {})
        stored_prev = chain.get("prev_hash", "")
        stored_hash = chain.get("row_hash", "")

        if stored_prev != prev_hash:
            return (False, idx)

        expected_hash = compute_row_hash(
            prev_hash,
            entry["stage"],
            entry["ok"],
            entry.get("signal_id"),
            detail,
        )
        if stored_hash != expected_hash:
            return (False, idx)

        prev_hash = stored_hash

    return (True, len(entries))
