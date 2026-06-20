"""Bug condition exploration test for BUG 1 — selftest crash (deploy-blocker-fixes).

Property 1 (Bug Condition): Selftest Returns Without Crashing.

This test encodes the EXPECTED post-fix behavior described in design.md
(Correctness Property 1, Bug Condition C1) and bugfix.md requirements 1.1, 1.2,
2.1, 2.2:

    run_guardrail_selftest() SHALL return a list[str] (empty on healthy code)
    WITHOUT raising pydantic.ValidationError, while still exercising the
    GR-02/GR-03/GR-04 gate-effectiveness checks.

On the UNFIXED code this test MUST FAIL: building a known-bad SignalCandidate
(e.g. BUY with stop_loss > entry_limit for the GR-02 check) trips the
construction-time `model_validator` and raises pydantic.ValidationError, which
escapes run_guardrail_selftest() at src/rtrade/guardrails/selftest.py instead of
returning a list. That failure confirms the bug exists.

DO NOT fix the code or this test when it fails — the failure is the success case
for this exploration step.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**
"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from rtrade.guardrails.selftest import run_guardrail_selftest


def test_selftest_returns_list_without_crashing() -> None:
    """run_guardrail_selftest() returns list[str] (empty when healthy), no crash.

    Scoped PBT: this is a deterministic bug, so the property is exercised on the
    concrete case of calling run_guardrail_selftest() against the current healthy
    code. The selftest internally builds known-bad candidates to exercise the
    GR-02/GR-03/GR-04 gate checks; on the fixed code that construction must not
    raise ValidationError out of the function.
    """
    try:
        problems = run_guardrail_selftest()
    except ValidationError as exc:  # pragma: no cover - failure path proves the bug
        pytest.fail(
            "BUG 1 reproduced: run_guardrail_selftest() raised pydantic."
            f"ValidationError instead of returning a list[str]: {exc}"
        )

    # Property 1: a list is returned ...
    assert isinstance(problems, list)
    assert all(isinstance(p, str) for p in problems)

    # ... and on healthy code the list is empty (all GR-02/GR-03/GR-04 gate
    # checks ran and every illegal candidate was rejected).
    assert problems == [], f"Selftest reported problems: {problems}"
