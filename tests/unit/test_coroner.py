"""T30: Tests for LLM Coroner."""

import pytest
from pydantic import ValidationError

from rtrade.llm.coroner import FAILURE_MODES, CoronerReport


def test_valid_report() -> None:
    r = CoronerReport(
        failure_mode="false_breakout",
        explanation_id="x" * 30,
        confidence=0.8,
    )
    assert r.failure_mode == "false_breakout"


def test_invalid_failure_mode() -> None:
    with pytest.raises(ValidationError):
        CoronerReport(
            failure_mode="invalid_mode",
            explanation_id="x" * 30,
            confidence=0.5,
        )


def test_all_modes_valid() -> None:
    for mode in FAILURE_MODES:
        r = CoronerReport(
            failure_mode=mode,
            explanation_id="x" * 30,
            confidence=0.5,
        )
        assert r.failure_mode == mode


def test_confidence_range() -> None:
    with pytest.raises(ValidationError):
        CoronerReport(
            failure_mode="unknown",
            explanation_id="x" * 30,
            confidence=1.5,
        )
