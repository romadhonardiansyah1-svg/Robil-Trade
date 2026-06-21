"""Keyed-HMAC model integrity (lawan pickle RCE) — S13 / C3.

An UNKEYED hash sidecar gives ZERO tamper protection: an attacker who can
overwrite the model file simply recomputes the plain hash and rewrites the
sidecar, so the check always passes and the malicious pickle is loaded. The
only defence is a *keyed* MAC whose secret the attacker does not have.

save_model → joblib.dump + write `hmac.new(key, file_bytes, sha256)` sidecar (`.hmac`).
load_model → recompute HMAC with the SAME key, compare_digest vs sidecar, then load.

The secret key is NEVER stored beside the model. Controls fail CLOSED:
- no key (save or load)  → refuse
- no `.hmac` sidecar      → refuse
- HMAC mismatch           → refuse (possible tamper)

Legacy unkeyed `.sha256` models are intentionally unloadable — they were never
actually protected. The next training run re-saves them under the HMAC scheme.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_NO_KEY_MSG = (
    "MODEL_HMAC_KEY tidak tersedia — menolak (fail closed): "
    "sidecar tanpa kunci rahasia tidak memberi proteksi tamper apa pun"
)


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".hmac")


def _resolve_key(hmac_key: str | bytes | None) -> bytes | None:
    """Resolve the HMAC key: explicit arg first, else config/env. Empty → None."""
    if hmac_key is not None:
        if isinstance(hmac_key, bytes):
            return hmac_key or None
        return hmac_key.encode("utf-8") if hmac_key else None
    # Fallback: read from Secrets (env MODEL_HMAC_KEY / .env), keeping the
    # explicit-arg path above for tests and callers that thread the key.
    from rtrade.core.config import Secrets

    key = Secrets().model_hmac_key
    return key.encode("utf-8") if key else None


def _hmac_file(path: Path, key: bytes) -> str:
    mac = hmac.new(key, digestmod=hashlib.sha256)
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            mac.update(chunk)
    return mac.hexdigest()


def save_model(obj: Any, path: Path, *, hmac_key: str | bytes | None = None) -> None:
    """Save model + keyed-HMAC integrity sidecar. No key → refuse."""
    import joblib

    key = _resolve_key(hmac_key)
    if key is None:
        raise RuntimeError(_NO_KEY_MSG)

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(obj, path)
    digest = _hmac_file(path, key)
    _sidecar(path).write_text(digest, encoding="utf-8")
    logger.info("model saved with keyed-HMAC sidecar", path=str(path))


def load_model(path: Path, *, hmac_key: str | bytes | None = None) -> Any:
    """Load HANYA bila HMAC sidecar cocok dengan kunci rahasia kita. Fail closed."""
    import joblib

    key = _resolve_key(hmac_key)
    if key is None:
        raise RuntimeError(_NO_KEY_MSG)

    sc = _sidecar(path)
    if not sc.exists():
        raise RuntimeError(f"model {path} tanpa sidecar HMAC — menolak memuat (fail closed)")

    expected = sc.read_text(encoding="utf-8").strip()
    actual = _hmac_file(path, key)
    if not hmac.compare_digest(expected, actual):
        raise RuntimeError(f"integritas model {path} GAGAL — menolak memuat (kemungkinan tamper)")
    return joblib.load(path)  # guarded by keyed-HMAC integrity check above
