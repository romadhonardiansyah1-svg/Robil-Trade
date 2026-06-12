"""Penyimpanan token OAuth terenkripsi di disk (Fernet).

Lokasi default: ~/.rtrade/tokens/<provider>.json (atau $RTRADE_TOKEN_DIR).
File chmod 0600. Dienkripsi dengan key dari env RTRADE_TOKEN_KEY (Fernet base64).
Jika RTRADE_TOKEN_KEY kosong: simpan plaintext TAPI log peringatan keras + chmod 0600.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StoredToken:
    access_token: str
    refresh_token: str | None
    expiry_epoch: float  # UTC epoch detik
    scopes: list[str]


def _token_dir() -> Path:
    base = os.environ.get("RTRADE_TOKEN_DIR")
    path = Path(base) if base else Path.home() / ".rtrade" / "tokens"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fernet():  # type: ignore[no-untyped-def]
    key = os.environ.get("RTRADE_TOKEN_KEY", "")
    if not key:
        return None
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def save_token(provider: str, token: StoredToken) -> None:
    path = _token_dir() / f"{provider}.json"
    raw = json.dumps(asdict(token)).encode()
    f = _fernet()
    data = f.encrypt(raw) if f is not None else raw
    if f is None:
        logger.warning("RTRADE_TOKEN_KEY kosong — token disimpan plaintext", provider=provider)
    path.write_bytes(data)
    if sys.platform != "win32":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600


def load_token(provider: str) -> StoredToken | None:
    path = _token_dir() / f"{provider}.json"
    if not path.exists():
        return None
    data = path.read_bytes()
    f = _fernet()
    try:
        raw = f.decrypt(data) if f is not None else data
        d = json.loads(raw)
        return StoredToken(**d)
    except Exception as exc:
        logger.error("gagal baca token store", provider=provider, error=str(exc))
        return None


def delete_token(provider: str) -> bool:
    """Hapus token store file. Return True jika file ada dan terhapus."""
    path = _token_dir() / f"{provider}.json"
    if path.exists():
        path.unlink()
        return True
    return False
