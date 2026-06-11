"""Analyst agent -- evaluates setup quality via LLM (PLAN 8.9.4 step 1).

Input: context pack → Output: AnalystAssessment
Model: trading-analyst (Gemini 3.1 Flash-Lite)
"""

from __future__ import annotations

from pathlib import Path

import structlog

from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.signals.schemas import AnalystAssessment

logger = structlog.get_logger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "analyst_system.md"
_MODEL = "gemini/gemini-3.1-flash-lite"


def _load_system_prompt() -> str:
    """Load system prompt from versioned markdown file."""
    return _PROMPT_PATH.read_text(encoding="utf-8")


async def run_analyst(
    client: LLMClient,
    pack: ContextPack,
    *,
    model: str = _MODEL,
) -> AnalystAssessment:
    """Run the Analyst agent on a context pack.

    Returns:
        AnalystAssessment with verdict, confidence, rationale, risks, sources.

    Raises:
        LLMOutputError: If output fails schema validation after retry.
        LLMUnavailableError: If all providers fail.
    """
    system_prompt = _load_system_prompt()
    user_prompt = (
        f"Analisa context pack berikut dan berikan penilaianmu:\n\n{pack.to_prompt_text()}"
    )

    result = await client.complete(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        response_schema=AnalystAssessment,
    )

    assessment = client.parse_response(result, AnalystAssessment)
    assert isinstance(assessment, AnalystAssessment)

    logger.info(
        "analyst completed",
        verdict=assessment.verdict,
        confidence=assessment.confidence_raw,
        sources_count=len(assessment.sources),
        latency_ms=f"{result.latency_ms:.0f}",
    )

    return assessment
