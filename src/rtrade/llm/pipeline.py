"""LLM pipeline -- Analyst -> Critic -> Verifier -> Confidence (PLAN 8.9).

Orchestrates the three-step LLM pipeline sequentially:
1. Analyst evaluates setup quality
2. Critic finds weaknesses
3. Verifier (deterministic) checks for hallucinations
4. Confidence formula computes final score

Timeout: 45s total pipeline. On failure/timeout:
- confidence >= 75 -> deterministic-only fallback (signal published)
- confidence < 75 -> ABSTAIN
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import time

import structlog

from rtrade.core.errors import LLMOutputError, LLMUnavailableError
from rtrade.llm.analyst import run_analyst
from rtrade.llm.budget_guard import BudgetGuard, BudgetState, BudgetStopReason
from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.llm.critic import run_critic
from rtrade.llm.verifier import verify
from rtrade.signals.schemas import (
    AnalystAssessment,
    CriticReview,
    SignalCandidate,
    VerifierReport,
)

logger = structlog.get_logger(__name__)


class PipelineDecision(StrEnum):
    """Final decision from the LLM pipeline."""

    PUBLISH = "PUBLISH"
    REJECTED = "REJECTED"
    ABSTAIN = "ABSTAIN"
    FALLBACK = "FALLBACK"  # deterministic-only (LLM failed but conf>=75)


@dataclass(frozen=True)
class PipelineResult:
    """Result from the full LLM pipeline."""

    decision: PipelineDecision
    confidence: float
    rationale: str
    key_risks: list[str]
    sources: list[str]
    llm_used: bool

    # Optional details for audit.
    assessment: AnalystAssessment | None = None
    review: CriticReview | None = None
    verifier_report: VerifierReport | None = None
    pipeline_latency_ms: float = 0.0

    # Budget guard (G-11): set when a per-scan cap was breached and the
    # pipeline aborted early. None when budgets were never exceeded (or when
    # no BudgetGuard was supplied, e.g. tests/dormant llm.enabled=false).
    budget_stop: BudgetStopReason | None = None


def compute_confidence(
    confluence_score: int,
    assessment: AnalystAssessment | None,
    review: CriticReview | None,
) -> float:
    """Compute final confidence using deterministic formula (PLAN 8.9.5).

    base        = confluence_score / 100
    adj_analyst = clamp(confidence_raw - 0.5, -0.15, +0.15) if CONFIRM else 0
    penalty     = 0.05 * count(severity >= med) (max 0.15)
    confidence  = clamp(base + adj_analyst - penalty, 0, 1)
    """
    base = confluence_score / 100.0

    # Analyst adjustment.
    adj_analyst = 0.0
    if assessment is not None and assessment.verdict == "CONFIRM":
        raw_adj = assessment.confidence_raw - 0.5
        adj_analyst = max(-0.15, min(0.15, raw_adj))

    # Critic penalty.
    penalty = 0.0
    if review is not None:
        med_or_high = sum(1 for ca in review.counter_arguments if ca.severity in ("med", "high"))
        penalty = min(0.05 * med_or_high, 0.15)

    confidence = max(0.0, min(1.0, base + adj_analyst - penalty))
    return round(confidence, 4)


async def run_llm_pipeline(
    candidate: SignalCandidate,
    pack: ContextPack,
    client: LLMClient,
    *,
    confidence_min: float = 0.55,
    deterministic_fallback_threshold: int = 75,
    analyst_model: str = "gemini/gemini-3.1-flash-lite",
    critic_model: str = "gemini/gemini-3.1-flash-lite",
    budget_guard: BudgetGuard | None = None,
    budget_state: BudgetState | None = None,
) -> PipelineResult:
    """Run the full LLM pipeline on a signal candidate.

    Args:
        candidate: The frozen SignalCandidate from P1 pipeline.
        pack: Context pack with all data + source_ids.
        client: LLM client for API calls.
        confidence_min: Minimum confidence for GR-09 (default 0.55).
        deterministic_fallback_threshold: If LLM fails but
            confluence >= this, publish as deterministic-only.
        analyst_model: Model to use for the Analyst agent.
        critic_model: Model to use for the Critic agent.
        budget_guard: Optional per-scan budget guard (G-11). When provided,
            token/USD/wall-clock/step caps are enforced after each model
            invocation; on breach the pipeline aborts cleanly (FALLBACK if
            confluence >= threshold else ABSTAIN). When ``None`` the pipeline
            behaves exactly as before (no enforcement) — this keeps existing
            callers/tests unchanged and is the dormant path while
            ``llm.enabled`` is false.
        budget_state: Optional pre-built per-scan budget state. Allows a caller
            to share one state across the initial call and a flagship
            escalation so caps accumulate across the whole scan. Created from
            ``budget_guard`` when omitted.

    Returns:
        PipelineResult with decision, confidence, and audit data.
    """
    start = time.monotonic()
    assessment: AnalystAssessment | None = None
    review: CriticReview | None = None
    verifier_report: VerifierReport | None = None

    # Budget guard setup (G-11). Enforcement is active only when a guard is
    # supplied; otherwise every budget hook below is a no-op.
    if budget_guard is not None and budget_state is None:
        budget_state = budget_guard.start_scan()
    alerted_80pct = False

    try:
        # Step 1: Analyst.
        tokens_before, usd_before = _usage_snapshot(client)
        assessment = await run_analyst(client, pack, model=analyst_model)
        if budget_guard is not None and budget_state is not None:
            reason = _record_usage(budget_guard, budget_state, client, tokens_before, usd_before)
            alerted_80pct = _maybe_alert_80pct(budget_guard, budget_state, alerted_80pct)
            if reason is not None:
                return _budget_abort(candidate, reason, deterministic_fallback_threshold, start)

        # Step 2: Critic.
        tokens_before, usd_before = _usage_snapshot(client)
        review = await run_critic(client, pack, assessment, model=critic_model)
        if budget_guard is not None and budget_state is not None:
            reason = _record_usage(budget_guard, budget_state, client, tokens_before, usd_before)
            alerted_80pct = _maybe_alert_80pct(budget_guard, budget_state, alerted_80pct)
            if reason is not None:
                return _budget_abort(candidate, reason, deterministic_fallback_threshold, start)

        # Step 3: Verifier (deterministic -- no model call, so no budget record).
        verifier_report = verify(pack, assessment, review)

    except (LLMOutputError, LLMUnavailableError) as exc:
        latency = (time.monotonic() - start) * 1000
        logger.warning(
            "llm pipeline failed, checking fallback",
            error=str(exc),
            confluence=candidate.confluence_score,
        )

        # Deterministic fallback.
        if candidate.confluence_score >= deterministic_fallback_threshold:
            return PipelineResult(
                decision=PipelineDecision.FALLBACK,
                confidence=candidate.confluence_score / 100.0,
                rationale=_deterministic_rationale(candidate),
                key_risks=["LLM unavailable - deterministic-only signal"],
                sources=[],
                llm_used=False,
                pipeline_latency_ms=latency,
            )

        return PipelineResult(
            decision=PipelineDecision.ABSTAIN,
            confidence=0.0,
            rationale=f"LLM pipeline failed: {exc}",
            key_risks=[],
            sources=[],
            llm_used=False,
            pipeline_latency_ms=latency,
        )

    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        logger.error("llm pipeline unexpected error", error=str(exc))
        return PipelineResult(
            decision=PipelineDecision.ABSTAIN,
            confidence=0.0,
            rationale=f"Unexpected error: {exc}",
            key_risks=[],
            sources=[],
            llm_used=False,
            pipeline_latency_ms=latency,
        )

    latency = (time.monotonic() - start) * 1000

    # --- Decision logic ---
    assert assessment is not None
    assert review is not None
    assert verifier_report is not None

    # Hallucination check.
    if verifier_report.hallucination_flag:
        logger.warning(
            "hallucination detected, abstaining",
            invalid_sources=len(verifier_report.invalid_source_ids),
            number_mismatches=len(verifier_report.number_mismatches),
        )
        return PipelineResult(
            decision=PipelineDecision.ABSTAIN,
            confidence=0.0,
            rationale="Hallucination detected by verifier",
            key_risks=assessment.key_risks,
            sources=assessment.sources,
            llm_used=True,
            assessment=assessment,
            review=review,
            verifier_report=verifier_report,
            pipeline_latency_ms=latency,
        )

    # VETO from analyst.
    if assessment.verdict == "VETO":
        return PipelineResult(
            decision=PipelineDecision.REJECTED,
            confidence=0.0,
            rationale=assessment.rationale_id,
            key_risks=assessment.key_risks,
            sources=assessment.sources,
            llm_used=True,
            assessment=assessment,
            review=review,
            verifier_report=verifier_report,
            pipeline_latency_ms=latency,
        )

    # VETO from critic.
    if review.recommendation == "VETO":
        return PipelineResult(
            decision=PipelineDecision.REJECTED,
            confidence=0.0,
            rationale=(
                f"Critic VETO: {review.counter_arguments[0].argument}"
                if review.counter_arguments
                else "Critic VETO"
            ),
            key_risks=assessment.key_risks,
            sources=assessment.sources,
            llm_used=True,
            assessment=assessment,
            review=review,
            verifier_report=verifier_report,
            pipeline_latency_ms=latency,
        )

    # Auto-VETO: critic has high severity with valid source_ids.
    for ca in review.counter_arguments:
        if ca.severity == "high":
            valid_sources = [sid for sid in ca.source_ids if sid in set(pack.source_ids)]
            if valid_sources:
                return PipelineResult(
                    decision=PipelineDecision.REJECTED,
                    confidence=0.0,
                    rationale=(
                        f"Auto-VETO: high severity argument with valid sources: {ca.argument}"
                    ),
                    key_risks=assessment.key_risks,
                    sources=assessment.sources,
                    llm_used=True,
                    assessment=assessment,
                    review=review,
                    verifier_report=verifier_report,
                    pipeline_latency_ms=latency,
                )

    # Compute confidence.
    confidence = compute_confidence(candidate.confluence_score, assessment, review)

    # GR-09: confidence floor.
    if confidence < confidence_min:
        return PipelineResult(
            decision=PipelineDecision.ABSTAIN,
            confidence=confidence,
            rationale=(f"Confidence {confidence:.2f} below minimum {confidence_min:.2f}"),
            key_risks=assessment.key_risks,
            sources=assessment.sources,
            llm_used=True,
            assessment=assessment,
            review=review,
            verifier_report=verifier_report,
            pipeline_latency_ms=latency,
        )

    # All checks passed -> PUBLISH.
    return PipelineResult(
        decision=PipelineDecision.PUBLISH,
        confidence=confidence,
        rationale=assessment.rationale_id,
        key_risks=assessment.key_risks,
        sources=assessment.sources,
        llm_used=True,
        assessment=assessment,
        review=review,
        verifier_report=verifier_report,
        pipeline_latency_ms=latency,
    )


def _usage_snapshot(client: LLMClient) -> tuple[int, float]:
    """Snapshot cumulative (tokens, usd) from the client for delta accounting."""
    stats = client.stats
    return int(stats["total_tokens"]), float(stats["total_cost_usd"])


def _record_usage(
    guard: BudgetGuard,
    state: BudgetState,
    client: LLMClient,
    tokens_before: int,
    usd_before: float,
) -> BudgetStopReason | None:
    """Record the cost of the last model invocation against the budget.

    Tokens/USD are sourced from the LLM client's cumulative stats (populated
    per call in ``LLMClient._attempt_loop`` from litellm usage + cost_per_token).
    We feed the *delta* since the pre-call snapshot so each step is charged
    exactly once. ``steps`` is always 1 per model invocation.
    """
    tokens_after, usd_after = _usage_snapshot(client)
    return guard.record(
        state,
        tokens=max(0, tokens_after - tokens_before),
        usd=max(0.0, usd_after - usd_before),
        steps=1,
    )


def _maybe_alert_80pct(guard: BudgetGuard, state: BudgetState, alerted: bool) -> bool:
    """Emit a best-effort one-shot warning when daily USD crosses 80%.

    Returns the updated ``alerted`` flag. Dedup is per-pipeline-run (best
    effort, non-fatal); cross-scan dedup is intentionally out of scope.
    """
    if not alerted and guard.at_80pct_daily(state):
        logger.warning("llm budget at 80% of daily cap", day_usd=state.day_usd)
        return True
    return alerted


def _budget_abort(
    candidate: SignalCandidate,
    reason: BudgetStopReason,
    deterministic_fallback_threshold: int,
    start: float,
) -> PipelineResult:
    """Abort the pipeline on a budget breach (G-11).

    Per plan P2-4: FALLBACK (deterministic-only) when confluence is high
    enough to publish without the LLM, otherwise ABSTAIN. No further model
    calls are made. The breached cap is recorded in ``budget_stop`` for audit.
    """
    latency = (time.monotonic() - start) * 1000
    logger.warning(
        "llm pipeline aborted on budget stop",
        reason=reason,
        confluence=candidate.confluence_score,
    )
    if candidate.confluence_score >= deterministic_fallback_threshold:
        return PipelineResult(
            decision=PipelineDecision.FALLBACK,
            confidence=candidate.confluence_score / 100.0,
            rationale=_deterministic_rationale(candidate),
            key_risks=[f"LLM budget stop ({reason}) - deterministic-only signal"],
            sources=[],
            llm_used=False,
            pipeline_latency_ms=latency,
            budget_stop=reason,
        )
    return PipelineResult(
        decision=PipelineDecision.ABSTAIN,
        confidence=0.0,
        rationale=f"LLM budget stop ({reason})",
        key_risks=[],
        sources=[],
        llm_used=False,
        pipeline_latency_ms=latency,
        budget_stop=reason,
    )


def _deterministic_rationale(candidate: SignalCandidate) -> str:
    """Build a rationale from confluence breakdown only (no LLM)."""
    bd = candidate.confluence_breakdown
    parts = []
    if bd.trend >= 15:
        parts.append(f"trend kuat ({bd.trend}/25)")
    if bd.momentum >= 12:
        parts.append(f"momentum mendukung ({bd.momentum}/20)")
    if bd.structure >= 12:
        parts.append(f"struktur baik ({bd.structure}/20)")
    if bd.volume >= 8:
        parts.append(f"volume konfirmasi ({bd.volume}/15)")
    if bd.macro >= 12:
        parts.append(f"makro mendukung ({bd.macro}/20)")

    if parts:
        return (
            "Sinyal deterministik (tanpa LLM): "
            + ", ".join(parts)
            + f". Confluence {candidate.confluence_score}/100."
        )
    return f"Sinyal deterministik (tanpa LLM). Confluence {candidate.confluence_score}/100."
