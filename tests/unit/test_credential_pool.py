"""CredentialPool: rotasi, cooldown, translate, classify (A6)."""

from __future__ import annotations

import asyncio

import pytest

from rtrade.llm.auth.api_key import ApiKeyProvider
from rtrade.llm.auth.pool import (
    AllCredentialsExhaustedError,
    CredentialPool,
    PooledCredential,
    classify_llm_error,
    model_flavor,
    translate_model,
)


def _pool(n: int = 3) -> CredentialPool:
    entries = [
        PooledCredential(cred_id=f"k{i}", flavor="gemini", credential=ApiKeyProvider(f"AIza{i}"))
        for i in range(n)
    ]
    return CredentialPool(entries)


def test_acquire_round_robin() -> None:
    pool = _pool(3)

    async def run() -> list[str]:
        return [(await pool.acquire()).cred_id for _ in range(4)]

    assert asyncio.run(run()) == ["k0", "k1", "k2", "k0"]


def test_failure_puts_credential_in_cooldown() -> None:
    pool = _pool(2)

    async def run() -> list[str]:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id)
        return [(await pool.acquire()).cred_id, (await pool.acquire()).cred_id]

    # k0 cooldown → hanya k1 yang muncul
    assert asyncio.run(run()) == ["k1", "k1"]


def test_all_cooldown_raises() -> None:
    pool = _pool(1)

    async def run() -> None:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id)
        await pool.acquire()

    with pytest.raises(AllCredentialsExhaustedError):
        asyncio.run(run())


def test_exclude_skips_tried() -> None:
    pool = _pool(2)

    async def run() -> str:
        return (await pool.acquire(exclude={"k0"})).cred_id

    assert asyncio.run(run()) == "k1"


def test_empty_and_duplicate_rejected() -> None:
    with pytest.raises(ValueError):
        CredentialPool([])
    e = PooledCredential(cred_id="x", flavor="gemini", credential=ApiKeyProvider("k"))
    with pytest.raises(ValueError):
        CredentialPool([e, e])


def test_model_flavor_and_translate() -> None:
    assert model_flavor("gemini/gemini-2.5-pro") == "gemini"
    assert translate_model("gemini/gemini-2.5-pro", "gemini") == "gemini/gemini-2.5-pro"
    assert translate_model("gemini/gemini-2.5-pro", "vertex_ai") == "vertex_ai/gemini-2.5-pro"
    assert translate_model("vertex_ai/gemini-2.5-pro", "gemini") == "gemini/gemini-2.5-pro"
    assert translate_model("gemini/gemini-2.5-pro", "anthropic") is None
    assert translate_model("tanpa-prefix", "gemini") is None


def test_classify_llm_error() -> None:
    class RateLimitError(Exception): ...

    class AuthenticationError(Exception): ...

    assert classify_llm_error(RateLimitError("x")) == "rate_limit"
    assert classify_llm_error(Exception("HTTP 429 too many requests")) == "rate_limit"
    assert classify_llm_error(Exception("RESOURCE_EXHAUSTED: quota")) == "rate_limit"
    assert classify_llm_error(AuthenticationError("bad")) == "auth"
    assert classify_llm_error(Exception("401 Unauthorized")) == "auth"
    assert classify_llm_error(RuntimeError("gw: tidak ada token valid. Belum login")) == "auth"
    assert classify_llm_error(Exception("connection reset")) == "other"
