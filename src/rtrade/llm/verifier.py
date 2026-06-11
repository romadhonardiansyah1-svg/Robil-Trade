"""Deterministic verifier -- anti-hallucination checks (PLAN 8.9.4 step 3).

This is NOT an LLM call. It is 100% deterministic regex + parsing:
  1. Every source_id in analyst+critic output exists in context pack.
  2. Every number quoted matches pack value (tolerance: 0.1% prices, 0.5 oscillators).
  3. No foreign symbol/instrument mentioned.

Any violation -> hallucination_flag=true -> final result = ABSTAIN.
"""

from __future__ import annotations

import re

import structlog

from rtrade.llm.context_pack import ContextPack
from rtrade.signals.schemas import (
    AnalystAssessment,
    CriticReview,
    VerifierReport,
)

logger = structlog.get_logger(__name__)

# Regex to extract numbers from text (integers and decimals).
_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\b")

# Oscillator fields (absolute tolerance 0.5).
_OSCILLATOR_FIELDS = frozenset(
    {
        "rsi",
        "adx",
        "plus_di",
        "minus_di",
        "atr_percentile",
        "confluence_score",
    }
)

# Known instrument symbols.
_KNOWN_SYMBOLS = frozenset(
    {
        "XAUUSD",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "BTCUSDT",
        "ETHUSDT",
    }
)


def verify(
    pack: ContextPack,
    assessment: AnalystAssessment,
    review: CriticReview,
) -> VerifierReport:
    """Run deterministic verification checks.

    Returns:
        VerifierReport with hallucination_flag and details.
    """
    invalid_source_ids: list[str] = []
    number_mismatches: list[str] = []
    checked_claims = 0

    pack_source_ids = set(pack.source_ids)

    # --- Check 1: source_id validity ---
    all_source_ids = _collect_source_ids(assessment, review)
    for sid in all_source_ids:
        checked_claims += 1
        if sid not in pack_source_ids:
            invalid_source_ids.append(sid)
            logger.warning("invalid source_id", source_id=sid)

    # --- Check 2: number accuracy ---
    pack_numbers = _extract_pack_numbers(pack)
    text_numbers = _extract_text_numbers(assessment, review)
    for num_str, value, context in text_numbers:
        checked_claims += 1
        match = _find_closest_match(value, pack_numbers)
        if match is None:
            # Number not found in pack at all — might be hallucinated.
            # Only flag if it looks like a price or indicator value.
            if _is_significant_number(value):
                number_mismatches.append(
                    f"number {num_str} ({value}) in '{context}' not found in pack"
                )
        else:
            matched_value, field_name = match
            tol = _get_tolerance(field_name, matched_value)
            if abs(value - matched_value) > tol:
                number_mismatches.append(
                    f"number {num_str} ({value}) vs pack "
                    f"{field_name}={matched_value} "
                    f"(tolerance {tol})"
                )

    # --- Check 3: foreign symbol detection ---
    foreign = _detect_foreign_symbols(assessment, review, pack.instrument["symbol"])
    for sym in foreign:
        number_mismatches.append(
            f"foreign symbol '{sym}' mentioned (expected {pack.instrument['symbol']})"
        )

    hallucination_flag = bool(invalid_source_ids or number_mismatches)

    if hallucination_flag:
        logger.warning(
            "hallucination detected",
            invalid_sources=len(invalid_source_ids),
            number_mismatches=len(number_mismatches),
            checked=checked_claims,
        )

    return VerifierReport(
        hallucination_flag=hallucination_flag,
        invalid_source_ids=invalid_source_ids,
        number_mismatches=number_mismatches,
        checked_claims=checked_claims,
    )


def _collect_source_ids(
    assessment: AnalystAssessment,
    review: CriticReview,
) -> list[str]:
    """Collect all source_ids from analyst and critic outputs."""
    ids: list[str] = list(assessment.sources)
    for ca in review.counter_arguments:
        ids.extend(ca.source_ids)
    return ids


def _extract_pack_numbers(
    pack: ContextPack,
) -> dict[str, float]:
    """Extract all numeric values from the pack as {field_name: value}."""
    numbers: dict[str, float] = {}

    # Candidate numbers.
    cand = pack.candidate
    for key in ("entry_limit", "stop_loss", "take_profit", "rr"):
        if cand.get(key) is not None:
            numbers[f"candidate.{key}"] = float(cand[key])

    # Indicator numbers.
    inds = pack.indicators
    for key, val in inds.items():
        if key == "bar_ts":
            continue
        if isinstance(val, dict) and val.get("value") is not None:
            numbers[f"ind.{key}"] = float(val["value"])

    # Confluence breakdown.
    breakdown = cand.get("confluence_breakdown", {})
    for key, val in breakdown.items():
        numbers[f"confluence.{key}"] = float(val)

    # Recent summary.
    summary = pack.recent_summary
    for key in ("return_24h", "return_7d", "range_position"):
        if summary.get(key) is not None:
            numbers[f"summary.{key}"] = float(summary[key])

    return numbers


def _extract_text_numbers(
    assessment: AnalystAssessment,
    review: CriticReview,
) -> list[tuple[str, float, str]]:
    """Extract (number_str, value, context) from LLM text outputs."""
    results: list[tuple[str, float, str]] = []

    # From analyst rationale.
    for m in _NUMBER_RE.finditer(assessment.rationale_id):
        results.append((m.group(), float(m.group()), "analyst.rationale"))

    # From analyst key_risks.
    for risk in assessment.key_risks:
        for m in _NUMBER_RE.finditer(risk):
            results.append((m.group(), float(m.group()), "analyst.risk"))

    # From critic arguments.
    for ca in review.counter_arguments:
        for m in _NUMBER_RE.finditer(ca.argument):
            results.append((m.group(), float(m.group()), "critic.argument"))

    return results


def _find_closest_match(
    value: float,
    pack_numbers: dict[str, float],
) -> tuple[float, str] | None:
    """Find the closest matching number in the pack."""
    best: tuple[float, str] | None = None
    best_diff = float("inf")

    for field_name, pv in pack_numbers.items():
        diff = abs(value - pv)
        if diff < best_diff:
            best_diff = diff
            best = (pv, field_name)

    return best


def _get_tolerance(field_name: str, value: float) -> float:
    """Get tolerance for a given field.

    - Oscillators (RSI, ADX, etc.): absolute 0.5
    - Prices and other values: relative 0.1% (min 0.01)
    """
    # Check if it's an oscillator field.
    for osc in _OSCILLATOR_FIELDS:
        if osc in field_name.lower():
            return 0.5

    # For confluence breakdown scores.
    if "confluence" in field_name.lower():
        return 0.5

    # For percentages (return_24h, etc.)
    if "return" in field_name.lower() or "position" in field_name.lower():
        return 0.5

    # Price tolerance: 0.1% relative, minimum 0.01.
    return max(abs(value) * 0.001, 0.01)


def _is_significant_number(value: float) -> bool:
    """Check if a number is likely a price or indicator value.

    Small integers (0-10) are often just ordinals or counts, not data.
    """
    # Skip very small integers that are likely just counters.
    return not (value == int(value) and 0 <= value <= 10)


def _detect_foreign_symbols(
    assessment: AnalystAssessment,
    review: CriticReview,
    expected_symbol: str,
) -> list[str]:
    """Detect mentions of symbols other than the expected one."""
    foreign: list[str] = []
    all_text = assessment.rationale_id

    for risk in assessment.key_risks:
        all_text += " " + risk
    for ca in review.counter_arguments:
        all_text += " " + ca.argument

    for sym in _KNOWN_SYMBOLS:
        if sym == expected_symbol:
            continue
        if sym in all_text:
            foreign.append(sym)

    return foreign
