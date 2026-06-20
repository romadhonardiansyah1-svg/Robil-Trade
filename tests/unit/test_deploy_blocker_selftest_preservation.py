"""Preservation tests for BUG 1 fix — selftest detection & guardrail invariants.

Property 7 (Preservation): Selftest Still Detects Broken Gates and Invariants Hold.

These tests capture BASELINE behavior that already holds on the UNFIXED code and
that the BUG 1 fix (isolating illegal-candidate construction inside selftest.py)
MUST NOT regress. They are written observation-first: each assertion mirrors the
actual behavior observed on the current code, so the whole module PASSES now and
must keep passing after the fix.

Scope (from design.md Preservation Requirements / bugfix.md 3.1, 3.2, 3.3):
  - The production signal path still rejects illegal candidates at construction
    via the Pydantic ``model_validator`` (GR-02/GR-03/GR-04) and the field
    constraints (GR-05 risk cap, distinct levels).
  - ``model_construct`` (validator bypass, GI-5) is NOT used anywhere on the
    production signal path (signals/, pipeline/, llm/).
  - The guardrail ``run_gate`` still DETECTS illegal candidates — the mechanism
    the selftest relies on to prove every gate rejects bad input.
  - The risk floors are unchanged: GR-03 RR>=1.5, GR-04 SL in [0.5,3.0]xATR,
    GR-05 risk<=2%.

NOTE (observation-first): on the UNFIXED code ``run_guardrail_selftest()`` itself
crashes (BUG 1), so we do NOT assert its end-to-end return value here — that is
the job of the exploration test. Instead we capture the underlying invariants the
selftest depends on, all of which genuinely pass on the unfixed code.

**Validates: Requirements 3.1, 3.2, 3.3**
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import pytest

from rtrade.core.constants import Action, Timeframe
from rtrade.guardrails.gate import run_gate
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _candidate_fields(**overrides: Any) -> dict[str, Any]:
    """Field dict for a healthy SignalCandidate (matches selftest defaults)."""
    fields: dict[str, Any] = {
        "candidate_id": "preservation",
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
    fields.update(overrides)
    return fields


def _make_candidate(**overrides: Any) -> SignalCandidate:
    """Build a candidate through the REAL constructor (production path)."""
    return SignalCandidate(**_candidate_fields(**overrides))


# ---------------------------------------------------------------------------
# 3.2 — production path rejects illegal candidates at construction
# ---------------------------------------------------------------------------


def test_buy_with_sl_above_entry_rejected_at_construction() -> None:
    """GR-02: BUY with SL > entry raises at construction (production validator)."""
    with pytest.raises(ValidationError, match="GR-02"):
        _make_candidate(
            action=Action.BUY,
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=2010.0,  # SL > entry for BUY = illegal
                take_profit=2020.0,
                atr_at_signal=5.0,
            ),
        )


def test_sell_with_wrong_direction_rejected_at_construction() -> None:
    """GR-02: SELL requires TP < entry < SL; a BUY-shaped SELL raises."""
    with pytest.raises(ValidationError, match="GR-02"):
        _make_candidate(
            action=Action.SELL,
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=1990.0,  # SL < entry for SELL = illegal
                take_profit=2020.0,
                atr_at_signal=5.0,
            ),
        )


def test_low_rr_rejected_at_construction() -> None:
    """GR-03: R:R below 1.5 raises at construction."""
    with pytest.raises(ValidationError, match="GR-03"):
        _make_candidate(
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=1990.0,
                take_profit=2005.0,  # RR = 0.5 < 1.5
                atr_at_signal=5.0,
            ),
        )


def test_out_of_range_sl_atr_rejected_at_construction() -> None:
    """GR-04: SL distance outside [0.5, 3.0]xATR raises at construction."""
    with pytest.raises(ValidationError, match="GR-04"):
        _make_candidate(
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=1980.0,  # 20 / 5 = 4.0x ATR > 3.0
                take_profit=2060.0,
                atr_at_signal=5.0,
            ),
        )


def test_excessive_risk_rejected_at_construction() -> None:
    """GR-05: risk_pct > 2.0 is rejected by the field constraint at construction."""
    with pytest.raises(ValidationError):
        _make_candidate(risk_pct=3.0)


def test_non_distinct_levels_rejected_at_construction() -> None:
    """LevelSet requires entry/SL/TP distinct (frozen invariant)."""
    with pytest.raises(ValidationError, match="distinct"):
        LevelSet(
            entry_limit=2000.0,
            stop_loss=2000.0,  # equal to entry
            take_profit=2020.0,
            atr_at_signal=5.0,
        )


# ---------------------------------------------------------------------------
# 3.3 — risk floors unchanged (boundary values still ACCEPTED, not over-tightened)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "sl", "tp", "atr", "risk", "note"),
    [
        (2000.0, 1990.0, 2015.0, 5.0, 1.0, "RR exactly 1.5 accepted"),
        (2000.0, 1990.0, 2016.0, 20.0, 1.0, "SL distance exactly 0.5xATR accepted"),
        (2000.0, 1985.0, 2025.0, 5.0, 1.0, "SL distance exactly 3.0xATR accepted"),
        (2000.0, 1990.0, 2020.0, 5.0, 2.0, "risk exactly 2.0% accepted"),
    ],
)
def test_risk_floor_boundaries_unchanged(
    entry: float, sl: float, tp: float, atr: float, risk: float, note: str
) -> None:
    """Boundary-legal candidates still construct — floors are not over-tightened."""
    candidate = _make_candidate(
        levels=LevelSet(
            entry_limit=entry,
            stop_loss=sl,
            take_profit=tp,
            atr_at_signal=atr,
        ),
        risk_pct=risk,
    )
    assert candidate.risk_pct == risk, note


# ---------------------------------------------------------------------------
# 3.1 — gate still detects broken / illegal candidates (selftest mechanism)
# ---------------------------------------------------------------------------


def test_valid_candidate_passes_gate() -> None:
    """Regression check: a valid candidate passes run_gate (selftest baseline)."""
    result = run_gate(_make_candidate())
    assert result.passed, [f.reason for f in result.failures]


def test_gate_detects_illegal_direction_candidate() -> None:
    """run_gate STILL rejects an illegal-direction candidate (GR-02 detection).

    The candidate is built via ``model_construct`` here in the TEST only (allowed
    in tests; GI-5 governs production). This mirrors how the selftest hands a
    known-bad object to the gate to prove the gate still rejects it.
    """
    bad = SignalCandidate.model_construct(
        **_candidate_fields(
            action=Action.BUY,
            levels=LevelSet(
                entry_limit=2000.0,
                stop_loss=2010.0,  # SL > entry for BUY = illegal
                take_profit=2020.0,
                atr_at_signal=5.0,
            ),
        )
    )
    result = run_gate(bad)
    assert not result.passed
    assert any(f.gate_id == "GR-02" for f in result.failures)


def test_gate_detects_excessive_risk_candidate() -> None:
    """run_gate STILL rejects risk_pct > 2.0 (GR-05 detection unchanged)."""
    bad = SignalCandidate.model_construct(**_candidate_fields(risk_pct=3.0))
    result = run_gate(bad)
    assert not result.passed
    assert any(f.gate_id == "GR-05" for f in result.failures)


# ---------------------------------------------------------------------------
# 3.2 — GI-5: no model_construct on the production signal path
# ---------------------------------------------------------------------------


def test_no_model_construct_on_production_signal_path() -> None:
    """GI-5: validator-bypass (model_construct) absent from the signal path.

    Scoped to the production signal-construction packages. The BUG 1 fix may add
    model_construct inside the startup selftest module (not the signal path), so
    selftest.py is intentionally excluded.
    """
    signal_path_dirs = [
        _REPO_ROOT / "src" / "rtrade" / "signals",
        _REPO_ROOT / "src" / "rtrade" / "pipeline",
        _REPO_ROOT / "src" / "rtrade" / "llm",
    ]
    offenders: list[str] = []
    for directory in signal_path_dirs:
        for py_file in directory.rglob("*.py"):
            if "model_construct" in py_file.read_text(encoding="utf-8"):
                offenders.append(str(py_file.relative_to(_REPO_ROOT)))
    assert offenders == [], f"model_construct found on production signal path: {offenders}"
