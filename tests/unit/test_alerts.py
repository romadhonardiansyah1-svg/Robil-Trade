"""Unit tests for alerting & health check system (P4-T4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rtrade.monitoring.alerts import (
    AlertLevel,
    AlertManager,
    AlertType,
)
from rtrade.monitoring.healthcheck import (
    CheckResult,
    HealthChecker,
    HealthStatus,
    SystemHealth,
)

# ============================================================================
# AlertManager tests
# ============================================================================


class TestAlertManagerCooldown:
    def test_first_alert_not_in_cooldown(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        assert not mgr._is_in_cooldown("test:key", AlertType.DISK_ALERT)

    def test_recent_alert_in_cooldown(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        mgr._last_sent["test:key"] = datetime.now(UTC)
        assert mgr._is_in_cooldown("test:key", AlertType.DISK_ALERT)

    def test_expired_cooldown_allows_resend(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        mgr._last_sent["test:key"] = datetime.now(UTC) - timedelta(hours=3)
        assert not mgr._is_in_cooldown("test:key", AlertType.DISK_ALERT)


class TestAlertManagerFormatting:
    def test_format_alert_contains_title(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        text = mgr._format_alert(
            AlertLevel.WARNING,
            "Test Title",
            "Test message body",
        )
        assert "Test Title" in text
        assert "Test message body" in text
        assert "ROBIL TRADE ALERT" in text

    def test_format_alert_with_details(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        text = mgr._format_alert(
            AlertLevel.CRITICAL,
            "Disk Full",
            "Low space",
            details={"used_pct": 95.2},
        )
        assert "used_pct" in text
        assert "95.2" in text


class TestAlertManagerSendLogic:
    @pytest.mark.asyncio
    async def test_disabled_manager_skips(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        result = await mgr.send_alert(
            AlertType.DISK_ALERT,
            AlertLevel.WARNING,
            "Test",
            "test",
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_scan_failed_under_threshold(self) -> None:
        """< 3 consecutive failures should NOT trigger alert."""
        mgr = AlertManager("token", "chat_id", enabled=False)
        result = await mgr.alert_scan_failed("XAUUSD", "timeout", 2)
        assert result is False

    def test_reset_scan_failures(self) -> None:
        mgr = AlertManager("token", "chat_id", enabled=False)
        mgr._consecutive_failures["scan:XAUUSD"] = 5
        mgr.reset_scan_failures("XAUUSD")
        assert "scan:XAUUSD" not in mgr._consecutive_failures


# ============================================================================
# HealthChecker tests
# ============================================================================


class TestCheckResult:
    def test_check_result_defaults(self) -> None:
        result = CheckResult(
            name="test",
            status=HealthStatus.HEALTHY,
        )
        assert result.name == "test"
        assert result.status == HealthStatus.HEALTHY
        assert result.message == ""
        assert result.latency_ms == 0.0

    def test_unhealthy_result(self) -> None:
        result = CheckResult(
            name="db",
            status=HealthStatus.UNHEALTHY,
            message="connection refused",
            latency_ms=1500.0,
        )
        assert result.status == HealthStatus.UNHEALTHY


class TestSystemHealth:
    def test_to_dict(self) -> None:
        health = SystemHealth(
            status=HealthStatus.HEALTHY,
            checks=[
                CheckResult(
                    name="db",
                    status=HealthStatus.HEALTHY,
                    message="ok",
                    latency_ms=5.2,
                ),
            ],
        )
        d = health.to_dict()
        assert d["status"] == "healthy"
        assert len(d["checks"]) == 1
        assert d["checks"][0]["name"] == "db"
        assert d["checks"][0]["latency_ms"] == 5.2

    def test_degraded_with_unhealthy_check(self) -> None:
        """If any check is UNHEALTHY, overall should reflect it."""
        checks = [
            CheckResult(name="db", status=HealthStatus.HEALTHY),
            CheckResult(name="redis", status=HealthStatus.UNHEALTHY),
        ]
        # The aggregation is done in run_all(), here we test
        # that the data structure works.
        health = SystemHealth(
            status=HealthStatus.UNHEALTHY,
            checks=checks,
        )
        d = health.to_dict()
        assert d["status"] == "unhealthy"


class TestHealthCheckerDisk:
    def test_disk_check_returns_result(self) -> None:
        checker = HealthChecker()
        result = checker.check_disk()
        assert result.name == "disk"
        assert result.status in (
            HealthStatus.HEALTHY,
            HealthStatus.DEGRADED,
            HealthStatus.UNHEALTHY,
        )
        assert "used_pct" in result.details
        assert "free_gb" in result.details
