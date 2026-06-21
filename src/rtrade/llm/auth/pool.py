"""Credential pool — rotasi + fallback lintas API key & akun OAuth (A6).

Satu pool berisi kredensial terurut (PooledCredential). Saat satu kredensial kena
rate limit / gagal auth, ia masuk cooldown (mesin: KeyManager) dan pemanggil pindah
ke kredensial berikutnya. cred_id yang dirotasi adalah label internal — BUKAN secret.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

import structlog

from rtrade.llm.auth.base import CredentialProvider
from rtrade.llm.key_manager import AllKeysExhaustedError, KeyManager

logger = structlog.get_logger(__name__)

_POOL_KEY = "llm_pool"

# Short transient retry hints like "30s", "45s", "60s", "2 m" -> stay rate_limit.
_SHORT_WINDOW_RE = re.compile(r"\b\d+\s*(?:s|m|sec|secs|min|mins)\b")


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
    """'subscription_limit' | 'rate_limit' | 'auth' | 'other' -> by exc name + message.

    Precedence (checked in this order):
      1. ``subscription_limit`` -- a usage/plan WINDOW that resets over HOURS,
         which warrants a long cooldown. Escalates ONLY when EITHER:
           (a) an explicit subscription/plan phrase is present (case-insensitive):
               "usage limit", "daily limit", "weekly limit", "monthly limit",
               "quota exceeded", "plan limit", "rate limit exceeded for your plan",
               "you've reached your usage", "you have hit your usage"; OR
           (b) a limit/quota error context (one of "limit", "quota", "429",
               "rate limit", "too many requests", "try again in") is COMBINED with
               a LONG-window reset indicator -- one of "hour", "hours", "day",
               "days", "week", "month", "tomorrow", "midnight", "reset at",
               "resets at" -- AND the message does NOT also carry a SHORT transient
               window (seconds/minutes, e.g. "30 seconds", "2 minutes", "45s").
         A BARE "quota"/"429"/"resource_exhausted", or a short transient retry hint
         like "try again in 30 seconds", does NOT escalate -- those stay
         ``rate_limit`` (see below). This avoids benching a credential for hours on
         an ordinary transient 429.
      2. ``rate_limit`` -- transient throttling: RateLimitError, "429",
         "rate limit", "resource_exhausted", "too many requests", bare "quota".
      3. ``auth`` -- AuthenticationError/PermissionDeniedError, "401"/"403",
         "unauthorized", "invalid api key", "belum login".
      4. ``other`` -- everything else.
    """
    name = type(exc).__name__
    msg = str(exc).lower()

    explicit_subscription_phrases = (
        "usage limit",
        "daily limit",
        "weekly limit",
        "monthly limit",
        "quota exceeded",
        "plan limit",
        "rate limit exceeded for your plan",
        "you've reached your usage",
        "you have hit your usage",
    )
    long_window_indicators = (
        "hour",
        "hours",
        "day",
        "days",
        "week",
        "month",
        "tomorrow",
        "midnight",
        "reset at",
        "resets at",
    )
    short_window_words = ("second", "seconds", "minute", "minutes")
    limit_context = (
        "limit" in msg
        or "quota" in msg
        or "429" in msg
        or "rate limit" in msg
        or "too many requests" in msg
        or "try again in" in msg
    )
    has_short_window = any(w in msg for w in short_window_words) or bool(
        _SHORT_WINDOW_RE.search(msg)
    )
    has_long_window = any(w in msg for w in long_window_indicators)

    is_explicit = any(p in msg for p in explicit_subscription_phrases)
    is_long_window_limit = limit_context and has_long_window and not has_short_window
    if is_explicit or is_long_window_limit:
        return "subscription_limit"
    if (
        name == "RateLimitError"
        or "429" in msg
        or "rate limit" in msg
        or "resource_exhausted" in msg
        or "too many requests" in msg
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
        auth_cooldown_seconds: int = 300,
        subscription_cooldown_seconds: int = 18000,
    ) -> None:
        if not entries:
            raise ValueError("CredentialPool tidak boleh kosong")
        ids = [e.cred_id for e in entries]
        if len(set(ids)) != len(ids):
            raise ValueError(f"cred_id duplikat di pool: {ids}")
        self._entries = list(entries)
        self._by_id = {e.cred_id: e for e in entries}
        # Adaptive cooldown TTLs keyed by failure kind.
        self._rate_cooldown_seconds = cooldown_seconds
        self._auth_cooldown_seconds = auth_cooldown_seconds
        self._subscription_cooldown_seconds = subscription_cooldown_seconds
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

    async def report_failure(
        self, cred_id: str, *, kind: str = "rate_limit", cooldown_seconds: int | None = None
    ) -> None:
        """Tandai kredensial gagal → cooldown.

        TTL cooldown dipilih berdasarkan ``kind`` saat ``cooldown_seconds`` None:
          - ``"subscription_limit"`` → ``subscription_cooldown_seconds`` (window panjang)
          - ``"auth"``               → ``auth_cooldown_seconds``
          - lainnya (``"rate_limit"``/``"other"``) → ``cooldown_seconds`` default
        Argumen ``cooldown_seconds`` eksplisit selalu meng-override pilihan by-kind.
        """
        if cooldown_seconds is not None:
            ttl = cooldown_seconds
        elif kind == "subscription_limit":
            ttl = self._subscription_cooldown_seconds
        elif kind == "auth":
            ttl = self._auth_cooldown_seconds
        else:
            ttl = self._rate_cooldown_seconds
        logger.warning(
            "credential failure — cooldown", cred_id=cred_id, kind=kind, cooldown_sec=ttl
        )
        await self._km.report_rate_limit(_POOL_KEY, cred_id, cooldown_seconds=ttl)
