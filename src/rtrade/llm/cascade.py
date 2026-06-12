"""T17: LLM model cascade — escalate ambiguous confidence to flagship model.

When the analyst model's confidence falls in the 'doubt band' (0.45–0.65),
the system escalates to the flagship model for a second opinion.
This prevents the cheap model from making borderline calls that could be
wrong in either direction.
"""

from __future__ import annotations

import structlog

from rtrade.llm.client import LLMCallResult, LLMClient

logger = structlog.get_logger(__name__)

# Default doubt band boundaries.
DOUBT_LOW = 0.45
DOUBT_HIGH = 0.65


async def cascade_complete(
    client: LLMClient,
    *,
    analyst_model: str,
    flagship_model: str,
    system_prompt: str,
    user_prompt: str,
    doubt_low: float = DOUBT_LOW,
    doubt_high: float = DOUBT_HIGH,
) -> LLMCallResult:
    """Run analyst model; escalate to flagship if confidence is ambiguous.

    Returns the final LLMResult (from whichever model answered).
    """
    result = await client.complete(
        model=analyst_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )

    # Try to parse confidence from the response.
    confidence = _extract_confidence(result.content)

    if confidence is not None and doubt_low <= confidence <= doubt_high:
        logger.info(
            "confidence in doubt band — escalating to flagship",
            analyst_confidence=confidence,
            analyst_model=analyst_model,
            flagship_model=flagship_model,
        )
        result = await client.complete(
            model=flagship_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    return result


def _extract_confidence(content: str) -> float | None:
    """Try to extract a confidence value from LLM response text."""
    import json
    import re

    # Try JSON parse first.
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "confidence" in data:
            return float(data["confidence"])
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Fallback: regex for "confidence": 0.XX or confidence: 0.XX
    match = re.search(r'"?confidence"?\s*[:=]\s*([0-9]*\.?[0-9]+)', content)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return None
