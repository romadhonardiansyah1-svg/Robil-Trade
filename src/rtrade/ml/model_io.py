"""Pemuatan model dengan verifikasi integritas (lawan pickle RCE) — S13.

save_model → simpan + sidecar SHA-256.
load_model → verifikasi sidecar sebelum joblib.load.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _sidecar(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".sha256")


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def save_model(obj: Any, path: Path) -> None:
    """Save model + integrity sidecar."""
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(obj, path)
    digest = _hash_file(path)
    _sidecar(path).write_text(digest, encoding="utf-8")
    logger.info("model saved with integrity sidecar", path=str(path))


def load_model(path: Path) -> Any:
    """Muat HANYA bila digest cocok dengan sidecar yang kita tulis sendiri."""
    import joblib

    sc = _sidecar(path)
    if not sc.exists():
        raise RuntimeError(f"model {path} tanpa sidecar integritas — menolak memuat")
    expected = sc.read_text(encoding="utf-8").strip()
    actual = _hash_file(path)
    if not hmac.compare_digest(expected, actual):
        raise RuntimeError(f"integritas model {path} GAGAL — menolak memuat (kemungkinan tamper)")
    return joblib.load(path)  # guarded by integrity check above
