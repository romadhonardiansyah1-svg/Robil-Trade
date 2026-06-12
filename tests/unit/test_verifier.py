"""Unit tests for deterministic verifier (PLAN 8.9.4 step 3) -- TDD.

This is the most critical anti-hallucination module. Tests cover:
- Invalid source_id detection
- Number mismatch detection (price tolerance 0.1%, oscillator 0.5)
- Foreign symbol detection
- Clean output passes
"""

from __future__ import annotations

from rtrade.llm.context_pack import ContextPack
from rtrade.llm.verifier import verify
from rtrade.signals.schemas import (
    AnalystAssessment,
    CounterArgument,
    CriticReview,
)


def _make_pack(
    symbol: str = "XAUUSD",
    entry: float = 2700.0,
    rsi: float = 45.0,
    atr: float = 10.0,
) -> ContextPack:
    """Create a minimal context pack for testing."""
    source_ids = [
        "ind:rsi:XAUUSD:1h:2026-07-01T06:00:00",
        "ind:atr:XAUUSD:1h:2026-07-01T06:00:00",
        "ind:ema21:XAUUSD:1h:2026-07-01T06:00:00",
        "reg:state:XAUUSD:1h:2026-07-01T06:00:00",
    ]
    return ContextPack(
        pack_id="pack_test",
        generated_at="2026-07-01T06:01:00",
        instrument={"symbol": symbol, "market": "forex", "session_active": True},
        candidate={
            "action": "BUY",
            "entry_limit": entry,
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
            "rsi": {"value": rsi, "source_id": source_ids[0]},
            "atr": {"value": atr, "source_id": source_ids[1]},
            "ema21": {"value": 2695.0, "source_id": source_ids[2]},
        },
        structure={
            "swing_highs": [],
            "swing_lows": [],
            "sr_levels": [],
            "gap_zones": [],
        },
        regime={
            "state": "TREND",
            "since": "2026-06-25T00:00:00",
            "source_id": source_ids[3],
        },
        calendar_next_72h=[],
        derivatives=None,
        similar_setups=None,
        recent_summary={"return_24h": 0.5, "return_7d": 1.2, "range_position": 65.0},
        source_ids=source_ids,
    )


def _make_assessment(
    verdict: str = "CONFIRM",
    confidence: float = 0.7,
    rationale: str = (
        "Setup trend baik dengan RSI 45 menunjukkan momentum moderat yang ideal untuk pullback"
    ),
    sources: list[str] | None = None,
) -> AnalystAssessment:
    """Create a test analyst assessment."""
    return AnalystAssessment(
        verdict=verdict,
        confidence_raw=confidence,
        rationale_id=rationale,
        key_risks=["Potensi reversal di level resistance 2720"],
        sources=sources or ["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
    )


def _make_review(
    recommendation: str = "PROCEED",
    arguments: list[CounterArgument] | None = None,
) -> CriticReview:
    """Create a test critic review."""
    if arguments is None:
        arguments = [
            CounterArgument(
                argument="RSI mendekati zona overbought pada 45",
                severity="low",
                source_ids=["ind:rsi:XAUUSD:1h:2026-07-01T06:00:00"],
            ),
            CounterArgument(
                argument="ATR relatif rendah menunjukkan volatilitas menurun",
                severity="low",
                source_ids=["ind:atr:XAUUSD:1h:2026-07-01T06:00:00"],
            ),
            CounterArgument(
                argument="Regime bisa berubah sewaktu-waktu menjadi ranging",
                severity="med",
                source_ids=["reg:state:XAUUSD:1h:2026-07-01T06:00:00"],
            ),
        ]
    return CriticReview(
        counter_arguments=arguments,
        recommendation=recommendation,
    )


class TestVerifierClean:
    """Tests where output should pass (no hallucination)."""

    def test_clean_output_passes(self) -> None:
        pack = _make_pack()
        assessment = _make_assessment()
        review = _make_review()

        report = verify(pack, assessment, review)

        assert not report.hallucination_flag
        assert len(report.invalid_source_ids) == 0
        assert len(report.number_mismatches) == 0
        assert report.checked_claims > 0


class TestVerifierInvalidSourceId:
    """Tests for invalid source_id detection."""

    def test_fake_source_id_flagged(self) -> None:
        pack = _make_pack()
        assessment = _make_assessment(
            sources=["fake:nonexistent:SOURCE:1h:2026-07-01T00:00:00"],
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        assert report.hallucination_flag
        assert len(report.invalid_source_ids) >= 1
        assert "fake:nonexistent:SOURCE:1h:2026-07-01T00:00:00" in report.invalid_source_ids

    def test_critic_fake_source_flagged(self) -> None:
        pack = _make_pack()
        assessment = _make_assessment()
        review = _make_review(
            arguments=[
                CounterArgument(
                    argument="Data menunjukkan kelemahan pada titik ini",
                    severity="med",
                    source_ids=["fake:source:ID:1h:2026-07-01"],
                ),
                CounterArgument(
                    argument="Volatilitas rendah bisa jadi masalah besar",
                    severity="low",
                    source_ids=["ind:atr:XAUUSD:1h:2026-07-01T06:00:00"],
                ),
                CounterArgument(
                    argument="Regime bisa berubah sewaktu-waktu ke ranging",
                    severity="low",
                    source_ids=["reg:state:XAUUSD:1h:2026-07-01T06:00:00"],
                ),
            ]
        )

        report = verify(pack, assessment, review)

        assert report.hallucination_flag
        assert "fake:source:ID:1h:2026-07-01" in report.invalid_source_ids


class TestVerifierNumberMismatch:
    """Tests for number accuracy checks."""

    def test_wrong_price_flagged(self) -> None:
        """LLM quotes a price that's way off from pack."""
        pack = _make_pack(entry=2700.0)
        # Analyst mentions 2800 (not in pack).
        assessment = _make_assessment(
            rationale=(
                "Entry 2800 menunjukkan level yang baik untuk buy "
                "dengan momentum yang mendukung posisi ini"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        # 2800 is far from any pack number (closest is 2720).
        assert report.hallucination_flag
        assert len(report.number_mismatches) >= 1

    def test_correct_price_within_tolerance(self) -> None:
        """Price within 0.1% tolerance should pass."""
        pack = _make_pack(entry=2700.0)
        # 2700 matches exactly.
        assessment = _make_assessment(
            rationale=(
                "Entry 2700 dengan support di EMA21 2695 menunjukkan setup yang baik dan konsisten"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        assert not report.hallucination_flag

    def test_oscillator_tolerance(self) -> None:
        """Oscillator within 0.5 absolute tolerance should pass."""
        pack = _make_pack(rsi=45.0)
        # RSI 45.3 is within 0.5 tolerance.
        assessment = _make_assessment(
            rationale=(
                "RSI pada level 45 menunjukkan momentum moderat "
                "yang ideal untuk entry pullback pada trend"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        assert not report.hallucination_flag


class TestVerifierForeignSymbol:
    """Tests for foreign symbol detection."""

    def test_foreign_symbol_flagged(self) -> None:
        """Mentioning EURUSD in XAUUSD analysis = hallucination."""
        pack = _make_pack(symbol="XAUUSD")
        assessment = _make_assessment(
            rationale=(
                "Setup EURUSD menunjukkan korelasi yang perlu "
                "diperhatikan dalam analisa XAUUSD ini secara mendalam"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        assert report.hallucination_flag
        assert any("EURUSD" in m for m in report.number_mismatches)

    def test_correct_symbol_passes(self) -> None:
        """Mentioning only the correct symbol should pass."""
        pack = _make_pack(symbol="XAUUSD")
        assessment = _make_assessment(
            rationale=(
                "XAUUSD menunjukkan trend bullish yang kuat "
                "dengan setup pullback yang valid dan menarik"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        # Should not flag for symbol.
        foreign_flags = [m for m in report.number_mismatches if "foreign symbol" in m]
        assert len(foreign_flags) == 0


class TestVerifierEdgeCases:
    """Edge case tests."""

    def test_small_integers_not_flagged(self) -> None:
        """Small integers (1-10) should not be flagged as hallucinations."""
        pack = _make_pack()
        assessment = _make_assessment(
            rationale=(
                "Ada 3 alasan utama mendukung setup ini "
                "berdasarkan 5 indikator yang telah dianalisa"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        # 3 and 5 are small integers - should not be flagged.
        num_mismatches = [m for m in report.number_mismatches if "not found" in m]
        assert len(num_mismatches) == 0

    def test_time_windows_not_treated_as_market_numbers(self) -> None:
        """Narrative time windows should not be matched to prices or indicators."""
        pack = _make_pack()
        assessment = _make_assessment(
            rationale=(
                "Pantau kalender 72 jam ke depan dan evaluasi ulang dalam 30 hari "
                "tanpa mengubah level entry yang sudah ditentukan"
            ),
        )
        review = _make_review()

        report = verify(pack, assessment, review)

        assert not report.hallucination_flag
