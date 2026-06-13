"""Credential pool — rotasi + fallback lintas API key & akun OAuth (A6).

Satu pool berisi kredensial terurut (PooledCredential). Saat satu kredensial kena
rate limit / gagal auth, ia masuk cooldown (mesin: KeyManager) dan pemanggil pindah
ke kredensial berikutnya. cred_id yang dirotasi adalah label internal — BUKAN secret.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from rtrade.llm.auth.base import CredentialProvider
from rtrade.llm.key_manager import AllKeysExhaustedError, KeyManager

logger = structlog.get_logger(__name__)

_POOL_KEY = "llm_pool"


class AllCredentialsExhaustedError(Exception):
    """Semua kredensial di pool sedang cooldown / sudah dicoba."""


@dataclass(frozen=True, slots=True)
class PooledCredential:
    """Satu kredensial siap pakai di dalam pool.

    flavor = prefix model litellm yang diterima kredensial ini:
    "gemini" | "vertex_ai" | "anthropic" | "openai" | "azure" | "xai"
    """

    cred_id: str
    flavor: str
    credential: CredentialProvider


def model_flavor(model: str) -> str:
    """Prefix provider dari nama model litellm ('gemini/x' → 'gemini')."""
    return model.split("/", 1)[0] if "/" in model else ""


def translate_model(model: str, flavor: str) -> str | None:
    """Nama model yang harus dipakai kredensial ber-flavor tsb; None = tak kompatibel.

    Translasi mekanis hanya gemini ↔ vertex_ai (katalog model Google sama).
    """
    prefix = model_flavor(model)
    if not prefix:
        return None
    if prefix == flavor:
        return model
    pair = {prefix, flavor}
    if pair == {"gemini", "vertex_ai"}:
        return f"{flavor}/{model.split('/', 1)[1]}"
    return None


def classify_llm_error(exc: BaseException) -> str:
    """'rate_limit' | 'auth' | 'other' — berbasis nama exception + isi pesan."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if (
        name == "RateLimitError"
        or "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "quota" in msg
    ):
        return "rate_limit"
    if (
        name in ("AuthenticationError", "PermissionDeniedError")
        or "401" in msg
        or "403" in msg
        or "unauthorized" in msg
        or "invalid api key" in msg
        or "belum login" in msg
    ):
        return "auth"
    return "other"


class CredentialPool:
    """Pool kredensial terurut dengan rotasi round-robin + cooldown."""

    def __init__(
        self,
        entries: list[PooledCredential],
        *,
        redis_client: Any | None = None,
        cooldown_seconds: int = 60,
    ) -> None:
        if not entries:
            raise ValueError("CredentialPool tidak boleh kosong")
        ids = [e.cred_id for e in entries]
        if len(set(ids)) != len(ids):
            raise ValueError(f"cred_id duplikat di pool: {ids}")
        self._entries = list(entries)
        self._by_id = {e.cred_id: e for e in entries}
        self._km = KeyManager(
            redis_client,
            {_POOL_KEY: ids},
            cooldown_seconds=cooldown_seconds,
        )

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> list[PooledCredential]:
        return list(self._entries)

    async def acquire(self, exclude: set[str] | None = None) -> PooledCredential:
        """Kredensial berikutnya yang tidak cooldown dan tidak di-exclude."""
        skip = exclude or set()
        for _ in range(self.size):
            try:
                cid = await self._km.get_next_key(_POOL_KEY)
            except AllKeysExhaustedError as exc:
                raise AllCredentialsExhaustedError(str(exc)) from exc
            if cid not in skip:
                return self._by_id[cid]
        raise AllCredentialsExhaustedError(
            f"semua {self.size} kredensial sudah dicoba di panggilan ini"
        )

    async def report_failure(self, cred_id: str, *, kind: str = "rate_limit") -> None:
        """Tandai kredensial gagal → cooldown. kind hanya untuk logging."""
        logger.warning("credential failure — cooldown", cred_id=cred_id, kind=kind)
        await self._km.report_rate_limit(_POOL_KEY, cred_id)
