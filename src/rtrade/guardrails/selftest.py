"""Startup guardrail integrity self-test (S11).

Build known-bad candidates → run_gate → all MUST be rejected.
Returns list of failures (empty = healthy).
"""

from __future__ import annotations

from datetime import UTC, datetime

from rtrade.core.constants import Action, Timeframe
from rtrade.guardrails.gate import run_gate
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate


def _make_candidate(**overrides: object) -> SignalCandidate:
    defaults = {
        "candidate_id": "selftest",
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


def run_guardrail_selftest() -> list[str]:
    """Run self-test: build illegal candidates, assert all rejected.

    Returns list of failure messages (empty = healthy).
    """
    problems: list[str] = []

    # Test 1: GR-09 — low confidence should fail
    good = _make_candidate()
    result = run_gate(good, confidence=0.30, confidence_min=0.55)
    if result.passed:
        problems.append("GR-09: low confidence 0.30 was NOT rejected")

    # Test 2: GR-10 — LLM mutated entry should fail
    original = _make_candidate()
    mutated = _make_candidate(
        levels=LevelSet(
            entry_limit=2000.1,
            stop_loss=1990.0,
            take_profit=2020.0,
            atr_at_signal=5.0,
        )
    )
    result = run_gate(mutated, original_candidate=original)
    if result.passed:
        problems.append("GR-10: entry mutation 2000→2000.1 was NOT rejected")

    # Test 3: GR-12 — too many signals today
    result = run_gate(good, signals_today=5, max_signals_per_day=3)
    if result.passed:
        problems.append("GR-12: 5 signals with max 3 was NOT rejected")

    # Test 4: verify a GOOD candidate passes (regression check)
    result = run_gate(good)
    if not result.passed:
        problems.append(
            f"REGRESSION: valid candidate rejected: {[f.reason for f in result.failures]}"
        )

    return problems
