"""Adversarial GR-10 tests: prove the guard REJECTS LLM-mutated numbers (S5).

This is not a test that GR-10 exists — it's a test that GR-10 WORKS
against an actively adversarial LLM that shifts entry by 1 pip.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rtrade.core.constants import Action, Timeframe
from rtrade.guardrails.gate import run_gate
from rtrade.signals.schemas import (
    AnalystAssessment,
    ConfluenceBreakdown,
    CriticReview,
    LevelSet,
    SignalCandidate,
)


def _make_candidate(**overrides: object) -> SignalCandidate:
    defaults = {
        "candidate_id": "test-123",
        "symbol": "XAUUSD",
        "timeframe": Timeframe.H1,
        "strategy": "ema_cross",
        "action": Action.BUY,
        "levels": LevelSet(
            entry_limit=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            atr_at_signal=5.0,
        ),
        "confluence_score": 70,
        "confluence_breakdown": ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        "risk_pct": 1.0,
        "position_size": 0.5,
        "valid_until": datetime.now(UTC),
        "bar_ts": datetime.now(UTC),
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SignalCandidate(**defaults)  # type: ignore[arg-type]


class TestGR10Adversarial:
    """S5: adversarial tests proving GR-10 catches manipulated numbers."""

    def test_entry_shifted_1_pip_rejected(self) -> None:
        """LLM shifts entry by 0.1 (1 pip for XAUUSD) → MUST fail GR-10."""
        original = _make_candidate()
        mutated = _make_candidate(
            levels=LevelSet(
                entry_limit=2000.1,  # shifted 1 pip
                stop_loss=1990.0,
                take_profit=2020.0,
                atr_at_signal=5.0,
            )
        )
        result = run_gate(mutated, original_candidate=original)
        assert not result.passed
        gr10_failures = [f for f in result.failures if f.gate_id == "GR-10"]
        assert len(gr10_failures) == 1
        assert "entry" in gr10_failures[0].reason.lower()

    def test_sl_widened_rejected(self) -> None:
        """LLM widens SL by 1 ATR → MUST fail GR-10."""
        original = _make_candidate()
        mutated = _make_candidate(
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=1988.0,  # widened from 1990 but still valid RR
                take_profit=2020.0,
                atr_at_signal=5.0,
            )
        )
        result = run_gate(mutated, original_candidate=original)
        assert not result.passed
        assert any(f.gate_id == "GR-10" for f in result.failures)

    def test_size_inflated_rejected(self) -> None:
        """LLM doubles position size → MUST fail GR-10."""
        original = _make_candidate()
        mutated = _make_candidate(position_size=1.0)  # doubled from 0.5
        result = run_gate(mutated, original_candidate=original)
        assert not result.passed
        gr10 = [f for f in result.failures if f.gate_id == "GR-10"]
        assert any("size" in f.reason.lower() for f in gr10)

    def test_identical_candidate_passes(self) -> None:
        """Unchanged candidate passes GR-10."""
        original = _make_candidate()
        result = run_gate(original, original_candidate=original)
        gr10_failures = [f for f in result.failures if f.gate_id == "GR-10"]
        assert len(gr10_failures) == 0


class TestConfidenceAdjustLimit:
    """S5: confidence_raw=1.0 from adversarial LLM cannot exceed base+0.15."""

    def test_max_confidence_capped(self) -> None:
        from rtrade.llm.pipeline import compute_confidence

        # Simulate adversarial LLM returning confidence=1.0
        adversarial_assessment = AnalystAssessment(
            verdict="CONFIRM",
            confidence_raw=1.0,  # adversarial max
            rationale_id="x" * 50,
            key_risks=["risk"],
            sources=["src1"],
        )
        result = compute_confidence(60, adversarial_assessment, None)
        # base=0.60, adj=min(0.15, 1.0-0.5)=+0.15 → max 0.75
        assert result <= 0.75 + 0.001

    def test_normal_confidence(self) -> None:
        from rtrade.llm.pipeline import compute_confidence

        normal_assessment = AnalystAssessment(
            verdict="CONFIRM",
            confidence_raw=0.70,
            rationale_id="y" * 50,
            key_risks=["risk"],
            sources=["src1"],
        )
        result = compute_confidence(60, normal_assessment, None)
        assert 0.55 <= result <= 0.80


class TestLLMOutputExtraFields:
    """S5: AnalystAssessment/CriticReview reject unknown keys."""

    def test_analyst_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            AnalystAssessment(
                verdict="CONFIRM",
                confidence_raw=0.8,
                rationale_id="a" * 50,
                key_risks=["r1"],
                sources=["s1"],
                evil_field="injected",  # type: ignore[call-arg]
            )

    def test_critic_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            CriticReview(
                counter_arguments=[
                    {"argument": "a" * 25, "severity": "low", "source_ids": ["s1"]},
                    {"argument": "b" * 25, "severity": "med", "source_ids": ["s2"]},
                    {"argument": "c" * 25, "severity": "high", "source_ids": ["s3"]},
                ],
                recommendation="VETO",
                evil_field="injected",  # type: ignore[call-arg]
            )
