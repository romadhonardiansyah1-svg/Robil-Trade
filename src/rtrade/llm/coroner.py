"""T30: LLM Coroner — automatic SL post-mortem classification.

When a signal hits SL, the coroner analyzes the price path and candidate
payload to classify the failure mode from a fixed taxonomy.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, field_validator
import structlog

from rtrade.llm.client import LLMClient

logger = structlog.get_logger(__name__)

FAILURE_MODES = (
    "false_breakout",
    "news_spike",
    "regime_flip",
    "sl_too_tight",
    "bad_fill",
    "unknown",
)


class CoronerReport(BaseModel):
    """Result of post-mortem analysis."""

    failure_mode: str
    explanation_id: str  # min 30 chars
    confidence: float  # 0..1

    @field_validator("failure_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in FAILURE_MODES:
            raise ValueError(f"failure_mode must be one of {FAILURE_MODES}, got '{v}'")
        return v

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError(f"confidence must be 0..1, got {v}")
        return v


_SYSTEM_PROMPT = (
    "Kamu adalah coroner trading. Klasifikasikan penyebab SL_HIT ke salah satu taksonomi berikut: "
    f"{', '.join(FAILURE_MODES)}. "
    "JANGAN menyebut angka di luar data yang diberikan. "
    'Jawab dalam JSON: {"failure_mode": str, "explanation_id": str (min 30 chars), "confidence": float 0..1}.'
)


async def run_coroner(
    client: LLMClient,
    *,
    model: str,
    candidate_payload: dict[str, Any],
    price_path: list[dict[str, Any]],
) -> CoronerReport:
    """Classify SL failure mode via LLM.

    Args:
        client: LLM client.
        model: Model to use.
        candidate_payload: Full candidate payload dict.
        price_path: 12 bars OHLC after fill (from DB).

    Returns:
        CoronerReport with classified failure mode.
    """
    user_prompt = json.dumps(
        {
            "candidate": candidate_payload,
            "price_path_after_fill": price_path[:12],
        },
        default=str,
    )

    result = await client.complete(
        model=model,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        response_schema=CoronerReport,
    )

    try:
        data = json.loads(result.content)
        return CoronerReport(**data)
    except (json.JSONDecodeError, Exception) as exc:
        logger.warning("coroner parse failed, defaulting to unknown", error=str(exc))
        return CoronerReport(
            failure_mode="unknown",
            explanation_id="Coroner LLM output could not be parsed into valid taxonomy report.",
            confidence=0.0,
        )
