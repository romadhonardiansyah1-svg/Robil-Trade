"""Unit tests for guardrail gate (PLAN §8.8)."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from rtrade.core.constants import Action, Regime, Timeframe
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate


def _make_candidate(
    action: Action = Action.BUY,
    entry: float = 2700.0,
    sl: float = 2690.0,
    tp: float = 2720.0,
    atr: float = 5.0,
    risk_pct: float = 1.0,
    confluence_score: int = 70,
) -> SignalCandidate:
    """Create a valid candidate for testing."""
    return SignalCandidate(
        candidate_id="test_001",
        symbol="XAUUSD",
        timeframe=Timeframe.H1,
        strategy="s1_trend_pullback",
        action=action,
        levels=LevelSet(
            entry_limit=entry,
            stop_loss=sl,
            take_profit=tp,
            atr_at_signal=atr,
        ),
        confluence_score=confluence_score,
        confluence_breakdown=ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        risk_pct=risk_pct,
        position_size=0.5,
        valid_until=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        bar_ts=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        created_at=datetime(2026, 7, 1, 6, 0, 30, tzinfo=UTC),
    )


class TestGuardrailGate:
    def test_all_pass(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        result = run_gate(
            candidate,
            regime=Regime.TREND,
            required_regime=Regime.TREND,
            now=datetime(2026, 7, 1, 6, 1, tzinfo=UTC),
        )
        assert result.passed

    def test_gr02_direction_fail_buy(self) -> None:
        """GR-02: BUY with SL above entry."""
        with pytest.raises(ValidationError, match="GR-02"):
            _make_candidate(action=Action.BUY, entry=2700, sl=2710, tp=2720)

    def test_gr03_rr_too_low(self) -> None:
        """GR-03: R:R below 1.5."""
        with pytest.raises(ValidationError, match="GR-03"):
            _make_candidate(entry=2700, sl=2695, tp=2705, atr=5.0)

    def test_gr05_risk_cap(self) -> None:
        """GR-05: Risk > 2.0%."""
        with pytest.raises(ValidationError):
            _make_candidate(risk_pct=3.0)

    def test_gr07_news_blackout(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        now = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
        events = [
            {
                "event": "Non-Farm Payrolls",
                "currency": "USD",
                "impact": "high",
                "event_time": (now + timedelta(minutes=15)).isoformat(),
            }
        ]
        result = run_gate(
            candidate,
            events=events,
            related_currencies=["USD"],
            regime=Regime.TREND,
            required_regime=Regime.TREND,
            now=now,
        )
        assert not result.passed
        assert any(f.gate_id == "GR-07" for f in result.failures)

    def test_gr08_crisis_blocked(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        result = run_gate(
            candidate,
            regime=Regime.CRISIS,
            now=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        )
        assert not result.passed
        assert any(f.gate_id == "GR-08" for f in result.failures)

    def test_gr08_wrong_regime(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        result = run_gate(
            candidate,
            regime=Regime.RANGE,
            required_regime=Regime.TREND,
            now=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        )
        assert not result.passed
        assert any(f.gate_id == "GR-08" for f in result.failures)

    def test_gr12_daily_limit(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        result = run_gate(
            candidate,
            signals_today=3,
            max_signals_per_day=3,
            regime=Regime.TREND,
            required_regime=Regime.TREND,
            now=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        )
        assert not result.passed
        assert any(f.gate_id == "GR-12" for f in result.failures)

    def test_gr13_negative_expectancy(self) -> None:
        from rtrade.guardrails.gate import run_gate

        candidate = _make_candidate()
        # 30 losing trades.
        outcomes = [-1.0] * 30
        result = run_gate(
            candidate,
            paper_outcomes=outcomes,
            regime=Regime.TREND,
            required_regime=Regime.TREND,
            now=datetime(2026, 7, 1, 6, 0, tzinfo=UTC),
        )
        assert not result.passed
        assert any(f.gate_id == "GR-13" for f in result.failures)
