"""Tests for guardrail selftest (S11)."""

from __future__ import annotations

from rtrade.guardrails.selftest import run_guardrail_selftest


def test_selftest_passes_on_healthy_code() -> None:
    """run_guardrail_selftest() should return empty list = healthy."""
    problems = run_guardrail_selftest()
    assert problems == [], f"Selftest reported problems: {problems}"
