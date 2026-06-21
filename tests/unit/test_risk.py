"""Unit tests for risk module (PLAN §8.7)."""

from datetime import UTC, datetime, timedelta

import pytest
import structlog

from rtrade.risk.limits import check_daily_limit, check_expectancy_guard
from rtrade.risk.news_filter import _parse_event_time, check_news_blackout
from rtrade.risk.sizing import (
    compute_kelly_fraction,
    compute_position_size,
    compute_with_kelly,
)


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

    def test_min_lot_floor_abstains_never_over_risks(self) -> None:
        """B3: when min-lot rounding floors the size to 0, abstain instead of bumping up.

        equity=100, risk_pct=1.0 → risk_amount budget = $1.00.
        position_size = 1.0 / 1000 = 0.001; floor(0.001/0.01)*0.01 = 0.0.
        One lot_step (0.01) would risk 0.01*1000 = $10 ≫ $1 budget (GR-05 breach),
        so we must abstain rather than bump up to one lot_step.
        """
        result = compute_position_size(
            equity=100.0, risk_pct=1.0, sl_distance=1000.0, lot_step=0.01
        )
        assert result.position_size == 0.0
        assert result.risk_amount_usd == 0.0
        assert result.method == "abstain_min_lot"
        assert result.kelly_size is None
        assert result.kelly_fraction is None

    def test_reports_true_risk_after_lot_rounding(self) -> None:
        """B3: a valid (rounded-down) size must report its TRUE USD risk, not the budget.

        equity=10_000, risk_pct=1.0 → budget = $100.
        position_size = 100/7 = 14.2857 → floor to 14.28.
        True risk = 14.28 * 7.0 = $99.96, which is <= the $100 budget.
        """
        result = compute_position_size(equity=10_000, risk_pct=1.0, sl_distance=7.0, lot_step=0.01)
        budget = 10_000 * (1.0 / 100)
        assert result.position_size == 14.28
        assert result.risk_amount_usd == round(result.position_size * 7.0, 2)
        assert result.risk_amount_usd == 99.96
        assert result.risk_amount_usd <= budget


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


class TestKellyWithSizing:
    def test_kelly_risk_clamped_to_gr05_cap(self) -> None:
        """B4: high-edge Kelly must be clamped to the 2% GR-05 cap.

        win_rate=0.9, avg_win=3.0, avg_loss=1.0, quarter-Kelly →
        kelly_f ≈ 0.217, so equity*kelly_f ≈ 21.7% ≫ 2%. The Kelly suggestion's
        true USD risk must never exceed 2% of equity.
        """
        equity = 10_000.0
        result = compute_with_kelly(
            equity=equity,
            risk_pct=1.0,
            sl_distance=10.0,
            win_rate=0.9,
            avg_win_r=3.0,
            avg_loss_r=1.0,
            lot_step=0.01,
        )
        cap = equity * 0.02
        assert result.kelly_risk_usd is not None
        assert result.kelly_risk_usd <= cap

    def test_kelly_risk_usd_reports_kelly_size_not_base(self) -> None:
        """B4: kelly_risk_usd reflects the Kelly suggestion's true risk, not the base size."""
        equity = 10_000.0
        sl_distance = 10.0
        result = compute_with_kelly(
            equity=equity,
            risk_pct=1.0,
            sl_distance=sl_distance,
            win_rate=0.9,
            avg_win_r=3.0,
            avg_loss_r=1.0,
            lot_step=0.01,
        )
        assert result.kelly_size is not None
        assert result.kelly_risk_usd is not None
        assert result.kelly_risk_usd == round(result.kelly_size * sl_distance, 2)
        # Base (fixed-pct) primary risk is 1% = $100, distinct from the clamped Kelly risk.
        assert result.risk_amount_usd == 100.0
        assert result.kelly_risk_usd != result.risk_amount_usd

    def test_kelly_risk_usd_none_without_edge(self) -> None:
        """B4: no Kelly edge → no advisory, kelly_risk_usd is None."""
        result = compute_with_kelly(
            equity=10_000.0,
            risk_pct=1.0,
            sl_distance=10.0,
            win_rate=0.3,
            avg_win_r=1.0,
            avg_loss_r=1.0,
            lot_step=0.01,
        )
        assert result.kelly_fraction is None
        assert result.kelly_size is None
        assert result.kelly_risk_usd is None


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

    def test_naive_event_time_string_warns_and_returns_utc(self) -> None:
        """B5: a NAIVE event_time string is assumed UTC but emits a LOUD warning."""
        with structlog.testing.capture_logs() as logs:
            parsed = _parse_event_time("2026-07-01T12:00:00", event_name="CPI")

        assert parsed is not None
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)  # UTC
        warnings = [r for r in logs if r.get("log_level") == "warning"]
        assert any("naive" in str(r.get("event", "")).lower() for r in warnings)

    def test_aware_event_time_string_does_not_warn(self) -> None:
        """B5: a tz-aware event_time string is normalized silently (no warning)."""
        with structlog.testing.capture_logs() as logs:
            parsed = _parse_event_time("2026-07-01T12:00:00+00:00", event_name="CPI")

        assert parsed is not None
        assert parsed.tzinfo is not None
        warnings = [r for r in logs if r.get("log_level") == "warning"]
        assert warnings == []

    def test_naive_high_impact_event_still_blocks_in_window(self) -> None:
        """B5 dedup: routing through _parse_event_time preserves blackout behavior.

        A naive high-impact event string in-window must still block (and warn).
        """
        now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        events: list[dict[str, object]] = [
            {
                "event": "Non-Farm Payrolls",
                "currency": "USD",
                "impact": "high",
                "event_time": "2026-07-01T12:15:00",  # naive, +15min, in-window
            }
        ]
        with structlog.testing.capture_logs() as logs:
            blocked, reason = check_news_blackout(events, ["USD"], now)

        assert blocked
        assert "GR-07" in (reason or "")
        warnings = [r for r in logs if r.get("log_level") == "warning"]
        assert any("naive" in str(r.get("event", "")).lower() for r in warnings)


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
