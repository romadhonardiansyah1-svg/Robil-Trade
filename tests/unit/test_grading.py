"""T19: Signal grading tests."""

from rtrade.signals.grading import Grade, grade_signal


class TestGrading:
    def test_grade_a(self) -> None:
        result = grade_signal(
            confluence_score=85,
            regime_match=True,
            edge_quality_score=82.0,
            has_high_impact_event=False,
            confidence=0.72,
        )
        assert result.grade == Grade.A

    def test_grade_b(self) -> None:
        result = grade_signal(
            confluence_score=70,
            regime_match=True,
            edge_quality_score=70.0,
            has_high_impact_event=True,  # news event → not A
            confidence=0.60,
        )
        assert result.grade == Grade.B

    def test_grade_c_low_confluence(self) -> None:
        result = grade_signal(
            confluence_score=60,
            regime_match=True,
            confidence=0.55,
        )
        assert result.grade == Grade.C

    def test_grade_c_no_regime(self) -> None:
        result = grade_signal(
            confluence_score=85,
            regime_match=False,
            confidence=0.70,
        )
        assert result.grade == Grade.C
        assert any("regime" in r for r in result.reasons)

    def test_grade_a_no_edge_quality(self) -> None:
        """Without edge quality data, A is still achievable."""
        result = grade_signal(
            confluence_score=85,
            regime_match=True,
            edge_quality_score=None,
            has_high_impact_event=False,
            confidence=0.70,
        )
        assert result.grade == Grade.A
