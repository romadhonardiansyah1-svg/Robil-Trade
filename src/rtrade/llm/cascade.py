"""F5: LLM model cascade — escalate ambiguous confidence to flagship model.

Replaced regex-based confidence extraction with deterministic
`should_escalate()` that checks PipelineResult.confidence against
escalation bands from config. Called from scan.py AFTER first
pipeline run, not inside the cascade module itself.
"""

from __future__ import annotations

import structlog

from rtrade.llm.pipeline import PipelineDecision, PipelineResult

logger = structlog.get_logger(__name__)

# Default doubt band boundaries (overridden from config).
DOUBT_LOW = 0.48
DOUBT_HIGH = 0.63


def should_escalate(
    result: PipelineResult,
    *,
    low: float = DOUBT_LOW,
    high: float = DOUBT_HIGH,
    flagship_model: str = "gemini/gemini-2.5-pro",
) -> bool:
    """Decide if the pipeline should be re-run with a flagship model.

    Returns True if:
    - The first pipeline used LLM (llm_used=True).
    - The decision is PUBLISH or FALLBACK.
    - The confidence falls in the doubt band [low, high].
    """
    if not result.llm_used:
        return False
    if result.decision not in (PipelineDecision.PUBLISH, PipelineDecision.FALLBACK):
        return False
    in_band = low <= result.confidence <= high
    if in_band:
        logger.info(
            "confidence in doubt band — suggesting escalation",
            confidence=result.confidence,
            doubt_low=low,
            doubt_high=high,
            flagship_model=flagship_model,
        )
    return in_band
