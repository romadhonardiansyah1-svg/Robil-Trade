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


def test_classify_subscription_limit() -> None:
    """Subscription/usage-WINDOW limits escalate to 'subscription_limit'.

    Each phrase is escalated either via an explicit subscription/plan phrase or
    via a limit/quota context combined with a LONG-window reset indicator
    (hours/days/midnight/etc.). The two long-window cases below
    ("...5 hours" and "...reset at midnight") deliberately keep their
    long-window indicator so they remain subscription_limit under the
    refined rule.
    """
    for phrase in (
        "You have hit your usage limit",
        "Daily limit reached",
        "weekly limit exceeded",
        "Monthly limit hit",
        "quota exceeded for this plan",
        "plan limit reached",
        "Please try again in 5 hours",  # long-window: "hours"
        "rate limit exceeded for your plan",
        "you've reached your usage cap",
        "limit reached, will reset at midnight",  # long-window: "reset at"/"midnight"
    ):
        assert classify_llm_error(Exception(phrase)) == "subscription_limit", phrase


def test_classify_subscription_limit_does_not_steal_rate_limit() -> None:
    """Bare quota / 429 / resource_exhausted must STILL be 'rate_limit'."""
    assert classify_llm_error(Exception("RESOURCE_EXHAUSTED: quota")) == "rate_limit"
    assert classify_llm_error(Exception("HTTP 429 too many requests")) == "rate_limit"
    assert classify_llm_error(Exception("quota")) == "rate_limit"


def test_classify_transient_429_stays_rate_limit() -> None:
    """Regression: SHORT transient throttling must NOT escalate to subscription_limit.

    The old matcher treated bare "try again in" and ("reset" + "limit") as a
    5-hour subscription window, mis-benching a credential for hours on an
    ordinary transient 429. These must all stay 'rate_limit'.
    """
    for phrase in (
        "rate limit, try again in 30 seconds",
        "rate limit; resets in 45s",
        "too many requests, retry in 2 minutes",
        "HTTP 429 too many requests",
        "RESOURCE_EXHAUSTED: quota",
        "quota",
    ):
        assert classify_llm_error(Exception(phrase)) == "rate_limit", phrase


def test_report_failure_forwards_cooldown_override() -> None:
    import time

    pool = _pool(1)

    async def run() -> float:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id, kind="subscription_limit", cooldown_seconds=18000)
        # Inspect the in-memory cooldown expiry (no Redis configured).
        return next(iter(pool._km._cooldowns.values()))

    expiry = asyncio.run(run())
    # Expiry should be ~now + 18000s, far beyond the default 60s.
    assert expiry > time.time() + 17000


def test_report_failure_default_cooldown() -> None:
    import time

    pool = _pool(1)

    async def run() -> float:
        c = await pool.acquire()
        await pool.report_failure(c.cred_id)
        return next(iter(pool._km._cooldowns.values()))

    expiry = asyncio.run(run())
    # Default cooldown is 60s; well under 1000s in the future.
    assert expiry < time.time() + 1000
