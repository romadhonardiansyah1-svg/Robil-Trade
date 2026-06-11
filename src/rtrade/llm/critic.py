"""Critic agent -- adversarial review of analyst assessment (PLAN 8.9.4 step 2).

Input: context pack + AnalystAssessment → Output: CriticReview
Model: trading-critic (Gemini 3.1 Flash-Lite; Claude Sonnet 4 when available)

The critic MUST provide >=3 counter_arguments. If any argument has
severity=high with valid source_ids → auto VETO.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.signals.schemas import AnalystAssessment, CriticReview

logger = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic_system.md"
_MODEL = "gemini/gemini-3.1-flash-lite"


def _load_system_prompt() -> str:
    """Load system prompt from versioned markdown file."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def run_critic(
    client: LLMClient,
    pack: ContextPack,
    assessment: AnalystAssessment,
    *,
    model: str = _MODEL,
) -> CriticReview:
    """Run the Critic agent on a context pack + analyst assessment.

    Returns:
        CriticReview with counter_arguments and recommendation.

    Raises:
        LLMOutputError: If output fails schema validation after retry.
        LLMUnavailableError: If all providers fail.
    """
    system_prompt = _load_system_prompt()

    assessment_json = json.dumps(
        {
            "verdict": assessment.verdict,
            "confidence_raw": assessment.confidence_raw,
            "rationale": assessment.rationale_id,
            "key_risks": assessment.key_risks,
            "sources": assessment.sources,
        },
        indent=2,
    )

    user_prompt = (
        f"Context pack:\n{pack.to_prompt_text()}\n\n"
        f"Penilaian Analyst:\n{assessment_json}\n\n"
        f"Berikan kritik dan counter-arguments terhadap penilaian di atas."
    )

    result = await client.complete(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=CriticReview,
    )

    review = client.parse_response(result, CriticReview)
    assert isinstance(review, CriticReview)

    logger.info(
        "critic completed",
        recommendation=review.recommendation,
        counter_args=len(review.counter_arguments),
        high_severity=sum(1 for ca in review.counter_arguments if ca.severity == "high"),
        latency_ms=f"{result.latency_ms:.0f}",
    )

    return review
