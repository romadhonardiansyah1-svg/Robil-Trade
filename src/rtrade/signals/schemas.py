"""Pydantic schemas for the signal pipeline (PLAN §9 — verbatim).

These are the data contracts for the entire system. Key invariants:
- LevelSet and SignalCandidate are FROZEN (immutable) — GR-10.
- Validators enforce GR-02 (direction), GR-03 (R:R floor), GR-04 (SL bounds).
- TradingSignal is the final output to user — numbers MUST match candidate.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rtrade.core.constants import Action, Timeframe

# ---------------------------------------------------------------------------
# Core signal schemas (PLAN §9)
# ---------------------------------------------------------------------------


class LevelSet(BaseModel):
    """Deterministic price levels. FROZEN — no mutation after creation (GR-10)."""

    model_config = ConfigDict(frozen=True)

    entry_limit: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    atr_at_signal: float = Field(gt=0)

    @model_validator(mode="after")
    def check_invariants(self) -> LevelSet:
        if len({self.entry_limit, self.stop_loss, self.take_profit}) != 3:
            raise ValueError("entry/SL/TP must be distinct")
        return self


class ConfluenceBreakdown(BaseModel):
    """Per-component confluence scores (PLAN §8.6)."""

    model_config = ConfigDict(frozen=True)

    trend: int = Field(ge=0, le=25)
    momentum: int = Field(ge=0, le=20)
    structure: int = Field(ge=0, le=20)
    volume: int = Field(ge=0, le=15)
    macro: int = Field(ge=0, le=20)

    @property
    def total(self) -> int:
        return self.trend + self.momentum + self.structure + self.volume + self.macro


class SignalCandidate(BaseModel):
    """Candidate from the deterministic pipeline. FROZEN (GR-10)."""

    model_config = ConfigDict(frozen=True)

    candidate_id: str
    symbol: str
    timeframe: Timeframe
    strategy: str
    action: Action  # BUY or SELL only (ABSTAIN doesn't become a candidate)
    levels: LevelSet
    confluence_score: int = Field(ge=0, le=100)
    confluence_breakdown: ConfluenceBreakdown
    risk_pct: float = Field(gt=0, le=2.0)  # GR-05
    position_size: float = Field(gt=0)
    valid_until: datetime
    bar_ts: datetime  # open time of the triggering bar (UTC)
    created_at: datetime

    @model_validator(mode="after")
    def check_direction_and_rr(self) -> SignalCandidate:
        e = self.levels.entry_limit
        sl = self.levels.stop_loss
        tp = self.levels.take_profit

        # GR-02: direction consistency.
        if self.action == Action.BUY and not (sl < e < tp):
            raise ValueError("GR-02: BUY requires SL < entry < TP")
        if self.action == Action.SELL and not (tp < e < sl):
            raise ValueError("GR-02: SELL requires TP < entry < SL")

        # GR-03: R:R floor.
        rr = abs(tp - e) / abs(e - sl)
        if rr < 1.5:
            raise ValueError(f"GR-03: RR {rr:.2f} < 1.5")

        # GR-04: SL distance in ATR multiples.
        atr_mult = abs(e - sl) / self.levels.atr_at_signal
        if not (0.5 <= atr_mult <= 3.0):
            raise ValueError(f"GR-04: SL distance {atr_mult:.2f}x ATR out of [0.5, 3.0]")

        return self


class GateFailure(BaseModel):
    """One guardrail gate failure."""

    gate_id: str
    reason: str


class GateResult(BaseModel):
    """Result from the guardrail gate (PLAN §8.8)."""

    passed: bool
    failures: list[GateFailure] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM layer schemas (P2 skeletons — not used in P1, but defined for types)
# ---------------------------------------------------------------------------


class AnalystAssessment(BaseModel):
    """Analyst agent output (P2)."""

    verdict: str = Field(pattern=r"^(CONFIRM|VETO|ABSTAIN)$")
    confidence_raw: float = Field(ge=0.0, le=1.0)
    rationale_id: str = Field(min_length=50)  # Bahasa Indonesia
    key_risks: list[str] = Field(min_length=1, max_length=5)
    sources: list[str] = Field(min_length=1)  # source_id from context pack


class CounterArgument(BaseModel):
    argument: str = Field(min_length=20)
    severity: str = Field(pattern=r"^(low|med|high)$")
    source_ids: list[str] = Field(min_length=1)


class CriticReview(BaseModel):
    """Critic agent output (P2)."""

    counter_arguments: list[CounterArgument] = Field(min_length=3)
    recommendation: str = Field(pattern=r"^(PROCEED|VETO|ABSTAIN)$")


class VerifierReport(BaseModel):
    """Verifier output — DETERMINISTIC, not LLM (P2)."""

    hallucination_flag: bool
    invalid_source_ids: list[str]
    number_mismatches: list[str]
    checked_claims: int


class TradingSignal(BaseModel):
    """Final output to user. Numbers MUST be identical to SignalCandidate (GR-10)."""

    signal_id: str
    candidate: SignalCandidate
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    key_risks: list[str]
    sources: list[str] = Field(min_length=1)  # GR-11
    llm_used: bool
    disclaimer: str  # Fixed text from PLAN §8.10
    published_at: datetime


# The mandatory disclaimer text (PLAN §8.10, §14.3).
DISCLAIMER_TEXT = (
    "Bukan nasihat keuangan. Trading berisiko tinggi; 74-89% akun "
    "retail merugi (data ESMA). Keputusan & eksekusi sepenuhnya "
    "tanggung jawab Anda."
)
