"""Tests for BudgetGuard wiring into the LLM pipeline (P2-4 / audit D2).

Verifies that ``run_llm_pipeline``:
  (a) aborts cleanly with a budget_stop when a supplied BudgetGuard's caps are
      breached by the (mocked) model calls -- FALLBACK if confluence is high
      enough, otherwise ABSTAIN; and
  (b) behaves exactly as before (full enforcement-free run) when no
      BudgetGuard is supplied (budget_guard=None), keeping all existing
      callers/tests unchanged. This is the dormant path (llm.enabled=false).

It also covers B2 (persist + seed daily LLM budget across scans): the scan
path's seed/persist helpers, backed by the Redis-keyed daily-cost store
(``KeyManager``), make the USD/day cap accumulate across consecutive scans
instead of resetting per scan.

All LLM calls are mocked -- deterministic, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rtrade.core.config import LLMBudgetSettings
from rtrade.core.constants import Action, Timeframe
from rtrade.llm.budget_guard import BudgetGuard
from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.llm.key_manager import KeyManager
from rtrade.llm.pipeline import PipelineDecision, run_llm_pipeline
from rtrade.pipeline.scan import _persist_scan_spend, _seed_daily_spend
from rtrade.signals.schemas import (
    AnalystAssessment,
    ConfluenceBreakdown,
    CounterArgument,
    CriticReview,
    LevelSet,
    SignalCandidate,
    VerifierReport,
)

_SRC = [
    "ind:rsi:XAUUSD:1h:2026-07-01T06:00:00",
    "ind:atr:XAUUSD:1h:2026-07-01T06:00:00",
]


def _make_candidate(confluence_score: int = 80) -> SignalCandidate:
    return SignalCandidate(
        candidate_id="test_budget_001",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        strategy="s1_trend_pullback",
        action=Action.BUY,
        levels=LevelSet(
            entry_limit=2700.0,
            stop_loss=2690.0,
            take_profit=2720.0,
            atr_at_signal=5.0,
        ),
        confluence_score=confluence_score,
        confluence_breakdown=ConfluenceBreakdown(
            trend=20,
            momentum=15,
            structure=15,
            volume=10,
            macro=10,
        ),
        risk_pct=1.0,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 0, 30, tzinfo=UTC),
    )


def _make_pack() -> ContextPack:
    return ContextPack(
        pack_id="pack_budget",
        generated_at="2026-07-01T06:01:00",
        instrument={"symbol": "XAUUSD", "market": "forex", "session_active": True},
        candidate={
            "action": "BUY",
            "entry_limit": 2700.0,
            "stop_loss": 2690.0,
            "take_profit": 2720.0,
            "rr": 2.0,
            "valid_until": "2026-07-01T12:00:00",
            "strategy": "s1_trend_pullback",
            "confluence_breakdown": {
                "trend": 20,
                "momentum": 15,
                "structure": 15,
                "volume": 10,
                "macro": 10,
            },
        },
        indicators={
            "bar_ts": "2026-07-01T06:00:00",
            "rsi": {"value": 45.0, "source_id": _SRC[0]},
            "atr": {"value": 10.0, "source_id": _SRC[1]},
        },
        structure={"swing_highs": [], "swing_lows": [], "sr_levels": [], "gap_zones": []},
        regime={"state": "TREND", "since": "2026-06-25", "source_id": _SRC[0]},
        calendar_next_72h=[],
        derivatives=None,
        similar_setups=None,
        recent_summary={"return_24h": 0.5},
        source_ids=_SRC,
    )


def _clean_assessment() -> AnalystAssessment:
    return AnalystAssessment(
        verdict="CONFIRM",
        confidence_raw=0.75,
        rationale_id=(
            "Setup trend pullback XAUUSD menunjukkan kualitas baik "
            "dengan RSI moderat dan trend yang terkonfirmasi"
        ),
        key_risks=["Potensi reversal di level resistance"],
        sources=[_SRC[0]],
    )


def _clean_review() -> CriticReview:
    return CriticReview(
        counter_arguments=[
            CounterArgument(
                argument="Volatilitas rendah bisa mengurangi peluang",
                severity="low",
                source_ids=[_SRC[1]],
            ),
            CounterArgument(
                argument="Regime bisa berubah menjadi ranging dalam waktu dekat",
                severity="low",
                source_ids=[_SRC[0]],
            ),
            CounterArgument(
                argument="Level support terdekat relatif jauh dari entry",
                severity="med",
                source_ids=[_SRC[1]],
            ),
        ],
        recommendation="PROCEED",
    )


def _clean_verifier_report() -> VerifierReport:
    return VerifierReport(
        hallucination_flag=False,
        invalid_source_ids=[],
        number_mismatches=[],
        checked_claims=5,
    )


def _tiny_token_guard() -> BudgetGuard:
    """Guard with a token cap small enough that one analyst call breaches it."""
    return BudgetGuard(
        LLMBudgetSettings(
            max_tokens_per_scan=100,
            max_usd_per_day=100.0,
            max_wall_seconds_per_scan=600.0,
            max_steps_per_scan=100,
        )
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, *, analyst_tokens: int) -> None:
    """Patch the three pipeline steps. Analyst charges ``analyst_tokens`` to client."""

    async def fake_analyst(
        client: LLMClient, pack: ContextPack, *, model: str
    ) -> AnalystAssessment:
        # Simulate the LLM client recording usage for this call. The real
        # client populates these in _attempt_loop; we mimic the delta so the
        # pipeline's stats-based budget accounting has something to charge.
        client._total_tokens += analyst_tokens
        client._total_cost += 0.01
        return _clean_assessment()

    async def fake_critic(
        client: LLMClient, pack: ContextPack, assessment: AnalystAssessment, *, model: str
    ) -> CriticReview:
        client._total_tokens += 50
        client._total_cost += 0.01
        return _clean_review()

    def fake_verify(
        pack: ContextPack, assessment: AnalystAssessment, review: CriticReview
    ) -> VerifierReport:
        return _clean_verifier_report()

    monkeypatch.setattr("rtrade.llm.pipeline.run_analyst", fake_analyst)
    monkeypatch.setattr("rtrade.llm.pipeline.run_critic", fake_critic)
    monkeypatch.setattr("rtrade.llm.pipeline.verify", fake_verify)


class TestBudgetGuardWiring:
    @pytest.mark.asyncio
    async def test_token_cap_breach_aborts_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(a) tiny token cap + high confluence -> FALLBACK with budget_stop=tokens."""
        _patch_pipeline(monkeypatch, analyst_tokens=5000)  # >> cap of 100
        candidate = _make_candidate(confluence_score=80)
        client = LLMClient(api_key="test")

        result = await run_llm_pipeline(
            candidate,
            _make_pack(),
            client,
            budget_guard=_tiny_token_guard(),
        )

        assert result.decision == PipelineDecision.FALLBACK
        assert result.budget_stop == "tokens"
        assert not result.llm_used
        # Critic was never reached: only the analyst step charged tokens.
        assert client.stats["total_tokens"] == 5000

    @pytest.mark.asyncio
    async def test_token_cap_breach_aborts_abstain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """(a) tiny token cap + low confluence -> ABSTAIN with budget_stop=tokens."""
        _patch_pipeline(monkeypatch, analyst_tokens=5000)
        candidate = _make_candidate(confluence_score=65)  # < fallback threshold 75
        client = LLMClient(api_key="test")

        result = await run_llm_pipeline(
            candidate,
            _make_pack(),
            client,
            budget_guard=_tiny_token_guard(),
        )

        assert result.decision == PipelineDecision.ABSTAIN
        assert result.budget_stop == "tokens"
        assert not result.llm_used

    @pytest.mark.asyncio
    async def test_no_budget_guard_runs_to_normal_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """(b) budget_guard=None -> no enforcement, normal decision, no budget_stop."""
        _patch_pipeline(monkeypatch, analyst_tokens=5000)  # would breach if enforced
        candidate = _make_candidate(confluence_score=70)
        client = LLMClient(api_key="test")

        result = await run_llm_pipeline(candidate, _make_pack(), client)

        assert result.decision == PipelineDecision.PUBLISH
        assert result.budget_stop is None
        assert result.llm_used

    @pytest.mark.asyncio
    async def test_within_budget_runs_normally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A generous guard does not interfere with a normal PUBLISH decision."""
        _patch_pipeline(monkeypatch, analyst_tokens=10)  # well within cap
        candidate = _make_candidate(confluence_score=70)
        client = LLMClient(api_key="test")
        guard = BudgetGuard(
            LLMBudgetSettings(
                max_tokens_per_scan=20000,
                max_usd_per_day=5.0,
                max_wall_seconds_per_scan=45.0,
                max_steps_per_scan=8,
            )
        )

        result = await run_llm_pipeline(candidate, _make_pack(), client, budget_guard=guard)

        assert result.decision == PipelineDecision.PUBLISH
        assert result.budget_stop is None
        assert result.llm_used


def _daily_guard() -> BudgetGuard:
    return BudgetGuard(
        LLMBudgetSettings(
            max_tokens_per_scan=1_000_000,
            max_usd_per_day=1.0,
            max_wall_seconds_per_scan=1_000.0,
            max_steps_per_scan=1_000,
        )
    )


class TestDailyBudgetSeedPersist:
    """B2: USD/day cap binds ACROSS scans via seed + persist (UTC-date keyed)."""

    @pytest.mark.asyncio
    async def test_daily_budget_binds_across_two_scans(self) -> None:
        # In-memory KeyManager (no Redis) -- exercises the graceful fallback path.
        store = KeyManager(redis_client=None, daily_budget_usd=1.0)
        guard = _daily_guard()

        # --- Scan 1: spends 0.7, under the 1.0 cap. ---
        seeded1 = await _seed_daily_spend(store)
        assert seeded1 == 0.0
        state1 = guard.start_scan(day_usd_seed=seeded1)
        assert guard.record(state1, usd=0.7) is None
        await _persist_scan_spend(store, state1.day_usd - seeded1)

        # Persisted spend is now visible to the next scan.
        assert await store.get_daily_cost() == pytest.approx(0.7)

        # --- Scan 2: a fresh BudgetState would reset to 0; seeding carries 0.7. ---
        seeded2 = await _seed_daily_spend(store)
        assert seeded2 == pytest.approx(0.7)
        state2 = guard.start_scan(day_usd_seed=seeded2)
        assert state2.day_usd == pytest.approx(0.7)
        # 0.7 (prior) + 0.5 (this scan) = 1.2 >= 1.0 -> cap binds ACROSS scans.
        assert guard.record(state2, usd=0.5) == "usd_day"

    @pytest.mark.asyncio
    async def test_seed_and_persist_no_store_is_graceful(self) -> None:
        # No store -> seed 0.0, persist is a no-op (no crash).
        assert await _seed_daily_spend(None) == 0.0
        await _persist_scan_spend(None, 0.5)

    @pytest.mark.asyncio
    async def test_persist_guards_negative_and_zero_delta(self) -> None:
        store = KeyManager(redis_client=None, daily_budget_usd=1.0)
        await _persist_scan_spend(store, -0.3)
        await _persist_scan_spend(store, 0.0)
        assert await store.get_daily_cost() == 0.0
