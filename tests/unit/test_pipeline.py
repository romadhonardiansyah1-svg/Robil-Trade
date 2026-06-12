"""Unit tests for LLM pipeline (PLAN 8.9.5).

4 scenarios with mocked LLM:
1. CONFIRM clean -> PUBLISH
2. VETO -> REJECTED
3. Hallucination -> ABSTAIN
4. Timeout -> deterministic fallback (conf>=75) / ABSTAIN
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from rtrade.core.constants import Action, Timeframe
from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.llm.pipeline import (
    PipelineDecision,
    compute_confidence,
    run_llm_pipeline,
)
from rtrade.signals.schemas import (
    AnalystAssessment,
    ConfluenceBreakdown,
    CounterArgument,
    CriticReview,
    LevelSet,
    SignalCandidate,
    VerifierReport,
)


def _make_candidate(confluence_score: int = 70) -> SignalCandidate:
    return SignalCandidate(
        candidate_id="test_001",
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
    source_ids = [
        "ind:rsi:XAUUSD:1h:2026-07-01T06:00:00",
        "ind:atr:XAUUSD:1h:2026-07-01T06:00:00",
    ]
    return ContextPack(
        pack_id="pack_test",
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
            "rsi": {"value": 45.0, "source_id": source_ids[0]},
            "atr": {"value": 10.0, "source_id": source_ids[1]},
        },
        structure={"swing_highs": [], "swing_lows": [], "sr_levels": [], "gap_zones": []},
        regime={"state": "TREND", "since": "2026-06-25", "source_id": source_ids[0]},
        calendar_next_72h=[],
        derivatives=None,
        similar_setups=None,
        recent_summary={"return_24h": 0.5},
        source_ids=source_ids,
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
        sources=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
    )


def _clean_review() -> CriticReview:
    return CriticReview(
        counter_arguments=[
            CounterArgument(
                argument="Volatilitas rendah bisa mengurangi peluang",
                severity="low",
                source_ids=["ind:atr:XAUUSD:1h:2026-07-01T06:00:00"],
            ),
            CounterArgument(
                argument="Regime bisa berubah menjadi ranging dalam waktu dekat",
                severity="low",
                source_ids=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
            ),
            CounterArgument(
                argument="Level support terdekat relatif jauh dari entry",
                severity="med",
                source_ids=["ind:atr:XAUUSD:1h:2026-07-01T06:00:00"],
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


class TestComputeConfidence:
    def test_base_only(self) -> None:
        conf = compute_confidence(70, None, None)
        assert conf == 0.7

    def test_with_analyst_confirm(self) -> None:
        assessment = _clean_assessment()  # confidence_raw=0.75
        # adj = clamp(0.75 - 0.5, -0.15, 0.15) = 0.15
        conf = compute_confidence(70, assessment, None)
        assert conf == pytest.approx(0.85, abs=0.01)

    def test_with_critic_penalty(self) -> None:
        review = _clean_review()  # 1 med severity
        # penalty = 0.05 * 1 = 0.05
        conf = compute_confidence(70, None, review)
        assert conf == pytest.approx(0.65, abs=0.01)

    def test_full_formula(self) -> None:
        assessment = _clean_assessment()  # adj = +0.15
        review = _clean_review()  # penalty = 0.05
        # base=0.7 + 0.15 - 0.05 = 0.80
        conf = compute_confidence(70, assessment, review)
        assert conf == pytest.approx(0.80, abs=0.01)

    def test_veto_analyst_no_adjustment(self) -> None:
        assessment = AnalystAssessment(
            verdict="VETO",
            confidence_raw=0.9,
            rationale_id="Setup berbahaya, ada divergence kuat pada momentum",
            key_risks=["reversal"],
            sources=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
        )
        # adj_analyst = 0 (not CONFIRM)
        conf = compute_confidence(70, assessment, None)
        assert conf == 0.7

    def test_clamp_bounds(self) -> None:
        # Very high: should clamp to 1.0.
        conf = compute_confidence(100, _clean_assessment(), None)
        assert conf <= 1.0

        # Very low: should clamp to 0.0.
        review = CriticReview(
            counter_arguments=[
                CounterArgument(
                    argument="Masalah serius pada setup ini yang harus diperhatikan",
                    severity="high",
                    source_ids=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
                ),
                CounterArgument(
                    argument="Kedua ini juga bermasalah dan perlu dipertimbangkan",
                    severity="high",
                    source_ids=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
                ),
                CounterArgument(
                    argument="Ketiga juga bermasalah dan sangat mengkhawatirkan",
                    severity="high",
                    source_ids=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
                ),
            ],
            recommendation="VETO",
        )
        conf = compute_confidence(10, None, review)
        assert conf >= 0.0


class TestPipelineScenarios:
    @pytest.mark.asyncio
    async def test_confirm_clean_publishes(self) -> None:
        """Scenario 1: CONFIRM + clean verifier -> PUBLISH."""
        candidate = _make_candidate(confluence_score=70)
        pack = _make_pack()
        client = LLMClient(api_key="test")

        with (
            patch("rtrade.llm.pipeline.run_analyst", new_callable=AsyncMock) as mock_analyst,
            patch("rtrade.llm.pipeline.run_critic", new_callable=AsyncMock) as mock_critic,
            patch("rtrade.llm.pipeline.verify") as mock_verify,
        ):
            mock_analyst.return_value = _clean_assessment()
            mock_critic.return_value = _clean_review()
            mock_verify.return_value = _clean_verifier_report()

            result = await run_llm_pipeline(candidate, pack, client)

        assert result.decision == PipelineDecision.PUBLISH
        assert result.confidence > 0.55
        assert result.llm_used

    @pytest.mark.asyncio
    async def test_veto_analyst_rejected(self) -> None:
        """Scenario 2: Analyst VETO -> REJECTED."""
        candidate = _make_candidate()
        pack = _make_pack()
        client = LLMClient(api_key="test")

        veto_assessment = AnalystAssessment(
            verdict="VETO",
            confidence_raw=0.2,
            rationale_id="Setup berbahaya: divergence negatif kuat pada multiple timeframe",
            key_risks=["divergence", "resistance"],
            sources=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
        )

        with (
            patch("rtrade.llm.pipeline.run_analyst", new_callable=AsyncMock) as mock_analyst,
            patch("rtrade.llm.pipeline.run_critic", new_callable=AsyncMock) as mock_critic,
            patch("rtrade.llm.pipeline.verify") as mock_verify,
        ):
            mock_analyst.return_value = veto_assessment
            mock_critic.return_value = _clean_review()
            mock_verify.return_value = _clean_verifier_report()

            result = await run_llm_pipeline(candidate, pack, client)

        assert result.decision == PipelineDecision.REJECTED
        assert result.llm_used

    @pytest.mark.asyncio
    async def test_hallucination_abstains(self) -> None:
        """Scenario 3: Verifier flags hallucination -> ABSTAIN."""
        candidate = _make_candidate()
        pack = _make_pack()
        client = LLMClient(api_key="test")

        halu_report = VerifierReport(
            hallucination_flag=True,
            invalid_source_ids=["fake:source"],
            number_mismatches=["wrong price"],
            checked_claims=3,
        )

        with (
            patch("rtrade.llm.pipeline.run_analyst", new_callable=AsyncMock) as mock_analyst,
            patch("rtrade.llm.pipeline.run_critic", new_callable=AsyncMock) as mock_critic,
            patch("rtrade.llm.pipeline.verify") as mock_verify,
        ):
            mock_analyst.return_value = _clean_assessment()
            mock_critic.return_value = _clean_review()
            mock_verify.return_value = halu_report

            result = await run_llm_pipeline(candidate, pack, client)

        assert result.decision == PipelineDecision.ABSTAIN
        assert result.llm_used

    @pytest.mark.asyncio
    async def test_timeout_fallback_high_confluence(self) -> None:
        """Scenario 4a: LLM timeout + confluence>=75 -> FALLBACK."""
        candidate = _make_candidate(confluence_score=80)
        pack = _make_pack()
        client = LLMClient(api_key="test")

        from rtrade.core.errors import LLMUnavailableError

        with patch(
            "rtrade.llm.pipeline.run_analyst",
            new_callable=AsyncMock,
            side_effect=LLMUnavailableError("timeout"),
        ):
            result = await run_llm_pipeline(candidate, pack, client)

        assert result.decision == PipelineDecision.FALLBACK
        assert not result.llm_used
        assert result.confidence == 0.8

    @pytest.mark.asyncio
    async def test_timeout_abstain_low_confluence(self) -> None:
        """Scenario 4b: LLM timeout + confluence<75 -> ABSTAIN."""
        candidate = _make_candidate(confluence_score=65)
        pack = _make_pack()
        client = LLMClient(api_key="test")

        from rtrade.core.errors import LLMUnavailableError

        with patch(
            "rtrade.llm.pipeline.run_analyst",
            new_callable=AsyncMock,
            side_effect=LLMUnavailableError("timeout"),
        ):
            result = await run_llm_pipeline(candidate, pack, client)

        assert result.decision == PipelineDecision.ABSTAIN
        assert not result.llm_used
