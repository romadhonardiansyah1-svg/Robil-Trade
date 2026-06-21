"""Unit tests for alerting & health check system (P4-T4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import time

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


class TestHealthCheckerTimeouts:
    """E6: DB/Redis connectivity probes must be bounded by a timeout.

    A hung backend must not make the healthcheck hang indefinitely — each
    probe must fail fast (within ~the configured timeout) and report UNHEALTHY.
    """

    @pytest.mark.asyncio
    async def test_database_probe_bounded_by_timeout(self) -> None:
        checker = HealthChecker(db_url="postgresql://unused", timeout_s=0.1)

        async def _hang() -> str | None:
            await asyncio.sleep(10)
            return "never"

        # Replace the real connection probe with one that hangs.
        checker._probe_database = _hang  # type: ignore[method-assign]

        start = time.monotonic()
        result = await checker.check_database()
        elapsed = time.monotonic() - start

        assert result.name == "database"
        assert result.status == HealthStatus.UNHEALTHY
        # Must be bounded by the timeout, not the 10s sleep.
        assert elapsed < 2.0, f"check_database hung for {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_redis_probe_bounded_by_timeout(self) -> None:
        checker = HealthChecker(redis_url="redis://unused", timeout_s=0.1)

        async def _hang() -> tuple[bool, str]:
            await asyncio.sleep(10)
            return True, "never"

        checker._probe_redis = _hang  # type: ignore[method-assign]

        start = time.monotonic()
        result = await checker.check_redis()
        elapsed = time.monotonic() - start

        assert result.name == "redis"
        assert result.status == HealthStatus.UNHEALTHY
        assert elapsed < 2.0, f"check_redis hung for {elapsed:.2f}s"


class TestHealthCheckerLitellmOptional:
    """D2: litellm check is optional — skip when URL empty, DEGRADED when unreachable."""

    @pytest.mark.asyncio
    async def test_empty_url_skips_litellm(self) -> None:
        """HealthChecker(litellm_url='').run_all() should NOT contain a 'litellm' check."""
        checker = HealthChecker(litellm_url="")
        result = await checker.check_litellm()
        assert result is None

    @pytest.mark.asyncio
    async def test_unreachable_url_returns_degraded(self) -> None:
        """With a URL set (but unreachable), litellm check should be DEGRADED."""
        checker = HealthChecker(litellm_url="http://127.0.0.1:49999")
        result = await checker.check_litellm()
        assert result is not None
        assert result.name == "litellm"
        assert result.status == HealthStatus.DEGRADED


# ============================================================================
# E3: alert delivery robust to arbitrary Markdown-special content
# ============================================================================


class TestTelegramRobustToMarkdownSpecials:
    """Dynamic content with Markdown specials must never break Telegram parsing.

    Fix chose plain text (parse_mode=None) so no content can produce a 400 and
    silently drop the alert.
    """

    @pytest.mark.asyncio
    async def test_send_telegram_payload_disables_markdown_parsing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        class _Resp:
            status_code = 200
            text = "ok"

        class _Client:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def post(self, url: str, json: dict[str, object]) -> _Resp:
                captured["payload"] = json
                return _Resp()

        monkeypatch.setattr("rtrade.monitoring.alerts.httpx.AsyncClient", _Client)

        mgr = AlertManager("token", "chat_id", enabled=True)
        text = mgr._format_alert(
            AlertLevel.WARNING,
            "Title _x_ *y* [z](",
            "err _x_ *y* [z](`backtick`",
            details={"key_a": "a*b_c[d]("},
        )
        ok = await mgr._send_telegram(text)

        assert ok is True
        payload = captured["payload"]
        assert isinstance(payload, dict)
        assert payload["parse_mode"] is None

    def test_format_alert_does_not_emit_literal_markdown_markers(self) -> None:
        """In plain-text mode the structural template must not show '*' markers."""
        mgr = AlertManager("token", "chat_id", enabled=False)
        text = mgr._format_alert(
            AlertLevel.WARNING,
            "Provider Down",
            "boom",
        )
        assert "*" not in text
        assert "ROBIL TRADE ALERT" in text
        assert "Provider Down" in text
