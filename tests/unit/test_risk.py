"""Unit tests for risk module (PLAN §8.7)."""

from datetime import UTC, datetime, timedelta

import pytest

from rtrade.risk.limits import check_daily_limit, check_expectancy_guard
from rtrade.risk.news_filter import check_news_blackout
from rtrade.risk.sizing import compute_kelly_fraction, compute_position_size


class TestPositionSizing:
    def test_basic_sizing(self) -> None:
        result = compute_position_size(equity=10_000, risk_pct=1.0, sl_distance=10.0)
        assert result.position_size == 10.0  # 100 / 10
        assert result.risk_amount_usd == 100.0

    def test_gr05_risk_cap(self) -> None:
        with pytest.raises(ValueError, match="GR-05"):
            compute_position_size(equity=10_000, risk_pct=3.0, sl_distance=10.0)

    def test_lot_step_rounding(self) -> None:
        result = compute_position_size(equity=10_000, risk_pct=1.0, sl_distance=7.0, lot_step=0.01)
        # 100/7 = 14.2857 → floor to 14.28
        assert result.position_size == 14.28

    def test_zero_equity_rejected(self) -> None:
        with pytest.raises(ValueError):
            compute_position_size(equity=0, risk_pct=1.0, sl_distance=10.0)


class TestKellyCriterion:
    def test_positive_edge(self) -> None:
        kelly = compute_kelly_fraction(win_rate=0.6, avg_win=2.0, avg_loss=1.0)
        assert kelly is not None
        assert kelly > 0

    def test_no_edge(self) -> None:
        kelly = compute_kelly_fraction(win_rate=0.3, avg_win=1.0, avg_loss=1.0)
        # 0.3 * 1.0 - 0.7 * 1.0 = -0.4 → no bet
        assert kelly is None

    def test_quarter_fraction(self) -> None:
        kelly_full = compute_kelly_fraction(win_rate=0.6, avg_win=2.0, avg_loss=1.0, fraction=1.0)
        kelly_quarter = compute_kelly_fraction(
            win_rate=0.6, avg_win=2.0, avg_loss=1.0, fraction=0.25
        )
        assert kelly_full is not None
        assert kelly_quarter is not None
        assert kelly_quarter == pytest.approx(kelly_full * 0.25, abs=0.001)


class TestNewsFilter:
    def test_no_events_ok(self) -> None:
        blocked, _reason = check_news_blackout(
            events=[],
            related_currencies=["USD"],
            now=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
        )
        assert not blocked

    def test_high_impact_within_window_blocked(self) -> None:
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        events = [
            {
                "event": "Non-Farm Payrolls",
                "currency": "USD",
                "impact": "high",
                "event_time": now + timedelta(minutes=15),
            }
        ]
        blocked, reason = check_news_blackout(
            events,
            ["USD"],
            now,
            before_min=30,
            after_min=15,
        )
        assert blocked
        assert "GR-07" in (reason or "")

    def test_low_impact_not_blocked(self) -> None:
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        events = [
            {
                "event": "Existing Home Sales",
                "currency": "USD",
                "impact": "low",
                "event_time": now + timedelta(minutes=10),
            }
        ]
        blocked, _ = check_news_blackout(events, ["USD"], now)
        assert not blocked

    def test_unrelated_currency_not_blocked(self) -> None:
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        events = [
            {
                "event": "ECB Rate Decision",
                "currency": "EUR",
                "impact": "high",
                "event_time": now + timedelta(minutes=10),
            }
        ]
        blocked, _ = check_news_blackout(events, ["USD"], now)
        assert not blocked

    def test_fomc_always_high(self) -> None:
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        events = [
            {
                "event": "FOMC Statement",
                "currency": "USD",
                "impact": "medium",  # provider says medium but we override
                "event_time": now + timedelta(minutes=20),
            }
        ]
        blocked, _ = check_news_blackout(events, ["USD"], now)
        # FOMC is always-high regardless of provider impact rating
        assert blocked


class TestLimits:
    def test_under_limit_ok(self) -> None:
        ok, _ = check_daily_limit(2, max_per_day=3)
        assert ok

    def test_at_limit_blocked(self) -> None:
        ok, reason = check_daily_limit(3, max_per_day=3)
        assert not ok
        assert "GR-12" in (reason or "")

    def test_expectancy_positive_ok(self) -> None:
        ok, _ = check_expectancy_guard([1.0, -0.5, 2.0] * 10, window=30)
        assert ok

    def test_expectancy_negative_blocked(self) -> None:
        ok, reason = check_expectancy_guard([-1.0] * 30, window=30)
        assert not ok
        assert "GR-13" in (reason or "")

    def test_not_enough_data_ok(self) -> None:
        ok, _ = check_expectancy_guard([-1.0] * 10, window=30)
        assert ok  # not enough data, allow
