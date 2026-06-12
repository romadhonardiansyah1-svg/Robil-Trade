"""F5: Tests for should_escalate (cascade escalation logic)."""

from rtrade.llm.cascade import should_escalate
from rtrade.llm.pipeline import PipelineDecision, PipelineResult


def _make_result(
    *,
    decision: PipelineDecision = PipelineDecision.PUBLISH,
    confidence: float = 0.55,
    llm_used: bool = True,
) -> PipelineResult:
    return PipelineResult(
        decision=decision,
        confidence=confidence,
        rationale="test",
        key_risks=[],
        sources=[],
        llm_used=llm_used,
    )


def test_escalate_in_doubt_band() -> None:
    r = _make_result(confidence=0.52)
    assert should_escalate(r, low=0.48, high=0.63) is True


def test_no_escalate_above_band() -> None:
    r = _make_result(confidence=0.70)
    assert should_escalate(r, low=0.48, high=0.63) is False


def test_no_escalate_below_band() -> None:
    r = _make_result(confidence=0.30)
    assert should_escalate(r, low=0.48, high=0.63) is False


def test_no_escalate_when_rejected() -> None:
    r = _make_result(decision=PipelineDecision.REJECTED, confidence=0.55)
    assert should_escalate(r) is False


def test_no_escalate_when_llm_not_used() -> None:
    r = _make_result(llm_used=False, confidence=0.55)
    assert should_escalate(r) is False


def test_escalate_at_low_boundary() -> None:
    r = _make_result(confidence=0.48)
    assert should_escalate(r, low=0.48, high=0.63) is True


def test_escalate_at_high_boundary() -> None:
    r = _make_result(confidence=0.63)
    assert should_escalate(r, low=0.48, high=0.63) is True


def test_escalate_fallback_decision() -> None:
    r = _make_result(decision=PipelineDecision.FALLBACK, confidence=0.55)
    assert should_escalate(r, low=0.48, high=0.63) is True


def test_no_escalate_abstain() -> None:
    r = _make_result(decision=PipelineDecision.ABSTAIN, confidence=0.55)
    assert should_escalate(r) is False
