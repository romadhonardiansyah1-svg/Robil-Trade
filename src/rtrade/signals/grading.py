"""T19: Signal grading — assigns A/B/C grade based on confluence + regime + edge quality.

Grade definitions (from IMPLEMENTATION_PLAN):
    A — Top quality: confluence ≥ 80, regime match, edge quality ≥ 80, no news risk.
    B — Standard quality: confluence ≥ 65, regime match, edge quality ≥ 65.
    C — Marginal: passes guardrails but below A/B thresholds.

Grade is informational — it appears in the Telegram message and in the
signal payload for later calibration analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Grade(StrEnum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True, slots=True)
class GradeResult:
    grade: Grade
    reasons: list[str]


def grade_signal(
    *,
    confluence_score: int,
    regime_match: bool,
    edge_quality_score: float | None = None,
    has_high_impact_event: bool = False,
    confidence: float = 0.5,
) -> GradeResult:
    """Grade a signal based on quality criteria."""
    reasons: list[str] = []

    # Grade A thresholds.
    is_a = (
        confluence_score >= 80
        and regime_match
        and (edge_quality_score is None or edge_quality_score >= 80)
        and not has_high_impact_event
        and confidence >= 0.65
    )

    if is_a:
        reasons.append(f"confluence={confluence_score}≥80")
        reasons.append("regime match")
        if edge_quality_score is not None:
            reasons.append(f"edge={edge_quality_score:.0f}≥80")
        reasons.append("no high-impact event nearby")
        return GradeResult(grade=Grade.A, reasons=reasons)

    # Grade B thresholds.
    is_b = (
        confluence_score >= 65
        and regime_match
        and (edge_quality_score is None or edge_quality_score >= 65)
        and confidence >= 0.55
    )

    if is_b:
        reasons.append(f"confluence={confluence_score}≥65")
        reasons.append("regime match")
        if edge_quality_score is not None:
            reasons.append(f"edge={edge_quality_score:.0f}≥65")
        return GradeResult(grade=Grade.B, reasons=reasons)

    # Grade C: everything else that passed guardrails.
    if confluence_score < 65:
        reasons.append(f"confluence={confluence_score}<65")
    if not regime_match:
        reasons.append("regime mismatch")
    if edge_quality_score is not None and edge_quality_score < 65:
        reasons.append(f"edge={edge_quality_score:.0f}<65")
    if confidence < 0.55:
        reasons.append(f"confidence={confidence:.2f}<0.55")

    return GradeResult(grade=Grade.C, reasons=reasons)
