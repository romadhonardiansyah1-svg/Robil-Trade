"""Telegram alerting for system events (PLAN P4-T4).

Alerts:
- Provider down >15 min
- Scan failed 3x consecutive
- LLM budget at 80%
- Disk usage >85%
- Service unhealthy

De-duplication: same alert type not re-sent within cooldown window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, ClassVar

import httpx
import structlog

logger = structlog.get_logger(__name__)


class AlertLevel(StrEnum):
    INFO = "INFO:"
    WARNING = "⚠️"
    CRITICAL = "🚨"


class AlertType(StrEnum):
    PROVIDER_DOWN = "provider_down"
    SCAN_FAILED = "scan_failed"
    BUDGET_ALERT = "budget_alert"
    DISK_ALERT = "disk_alert"
    SERVICE_UNHEALTHY = "service_unhealthy"
    BACKUP_FAILED = "backup_failed"
    KEY_EXHAUSTED = "key_exhausted"
    DATA_GAP = "data_gap"


class AlertManager:
    """Sends system alerts to Telegram with de-duplication.

    Each alert type has a cooldown period. The same alert won't be
    re-sent until the cooldown expires, preventing alert fatigue.
    """

    DEFAULT_COOLDOWNS: ClassVar[dict[AlertType, timedelta]] = {
        AlertType.PROVIDER_DOWN: timedelta(minutes=30),
        AlertType.SCAN_FAILED: timedelta(hours=2),
        AlertType.BUDGET_ALERT: timedelta(hours=4),
        AlertType.DISK_ALERT: timedelta(hours=2),
        AlertType.SERVICE_UNHEALTHY: timedelta(minutes=15),
        AlertType.BACKUP_FAILED: timedelta(hours=6),
        AlertType.KEY_EXHAUSTED: timedelta(minutes=10),
        AlertType.DATA_GAP: timedelta(hours=1),
    }

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        cooldowns: dict[AlertType, timedelta] | None = None,
        enabled: bool = True,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._enabled = enabled
        self._cooldowns = cooldowns or self.DEFAULT_COOLDOWNS
        self._last_sent: dict[str, datetime] = {}
        self._consecutive_failures: dict[str, int] = {}

    async def send_alert(
        self,
        alert_type: AlertType,
        level: AlertLevel,
        title: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        """Send an alert to Telegram.

        Args:
            alert_type: Type of alert for de-duplication.
            level: Severity level.
            title: Short alert title.
            message: Alert body.
            details: Optional metadata dict.
            force: Bypass cooldown if True.

        Returns:
            True if alert was sent, False if suppressed by cooldown.
        """
        if not self._enabled:
            logger.debug("alerting disabled, skipping", alert_type=alert_type)
            return False

        # Check cooldown.
        dedup_key = f"{alert_type}:{title}"
        if not force and self._is_in_cooldown(dedup_key, alert_type):
            logger.debug(
                "alert suppressed by cooldown",
                alert_type=alert_type,
                title=title,
            )
            return False

        # Format message.
        text = self._format_alert(level, title, message, details)

        # Send via Telegram Bot API.
        success = await self._send_telegram(text)

        if success:
            self._last_sent[dedup_key] = datetime.now(UTC)
            logger.info(
                "alert sent",
                alert_type=alert_type,
                level=level,
                title=title,
            )
        else:
            logger.error(
                "alert send failed",
                alert_type=alert_type,
                title=title,
            )

        return success

    async def alert_provider_down(self, provider: str, duration_min: float) -> bool:
        """Alert: data provider unreachable for >15 min."""
        return await self.send_alert(
            AlertType.PROVIDER_DOWN,
            AlertLevel.CRITICAL,
            f"Provider Down: {provider}",
            f"Provider {provider} tidak merespons selama "
            f"{duration_min:.0f} menit.\n"
            f"Data ingestion terganggu — sinyal mungkin tertunda.",
        )

    async def alert_scan_failed(self, instrument: str, error: str, consecutive: int) -> bool:
        """Alert: scan failed 3x consecutively. Rate limit errors suppressed."""
        key = f"scan:{instrument}"
        self._consecutive_failures[key] = consecutive
        if consecutive < 3:
            return False

        # Suppress rate limit errors — they are transient and self-resolving
        error_lower = error.lower()
        if "rate limit" in error_lower or "429" in error_lower:
            logger.debug("rate limit scan failure suppressed", instrument=instrument)
            return False

        return await self.send_alert(
            AlertType.SCAN_FAILED,
            AlertLevel.WARNING,
            f"Scan Failed: {instrument}",
            f"Scan {instrument} gagal {consecutive}x berturut-turut.\n"
            f"Error terakhir: {error[:200]}",
        )

    async def alert_budget(self, spent_usd: float, budget_usd: float) -> bool:
        """Alert: LLM budget at 80%+."""
        pct = (spent_usd / budget_usd * 100) if budget_usd > 0 else 0
        return await self.send_alert(
            AlertType.BUDGET_ALERT,
            AlertLevel.WARNING,
            "LLM Budget Alert",
            f"Pengeluaran LLM hari ini: ${spent_usd:.4f} "
            f"({pct:.0f}% dari budget ${budget_usd:.2f}).\n"
            f"Pipeline mungkin berhenti jika budget habis.",
        )

    async def alert_disk(self, used_pct: float, free_gb: float) -> bool:
        """Alert: disk usage >85%."""
        level = AlertLevel.CRITICAL if used_pct > 95 else AlertLevel.WARNING
        return await self.send_alert(
            AlertType.DISK_ALERT,
            level,
            "Disk Usage Alert",
            f"Disk usage: {used_pct:.1f}% — sisa {free_gb:.1f}GB.\n"
            f"Bersihkan log/backup lama atau resize disk.",
        )

    async def alert_service_unhealthy(self, service: str, message: str) -> bool:
        """Alert: a service is unhealthy."""
        return await self.send_alert(
            AlertType.SERVICE_UNHEALTHY,
            AlertLevel.CRITICAL,
            f"Service Unhealthy: {service}",
            f"Service {service} dalam status UNHEALTHY.\nDetail: {message}",
        )

    def reset_scan_failures(self, instrument: str) -> None:
        """Reset consecutive failure counter after successful scan."""
        key = f"scan:{instrument}"
        self._consecutive_failures.pop(key, None)

    def _is_in_cooldown(self, dedup_key: str, alert_type: AlertType) -> bool:
        """Check if an alert is within its cooldown period."""
        last = self._last_sent.get(dedup_key)
        if last is None:
            return False

        cooldown = self._cooldowns.get(alert_type, timedelta(minutes=15))
        return datetime.now(UTC) - last < cooldown

    def _format_alert(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Format alert as plain text.

        Sent with parse_mode=None so arbitrary dynamic content (error strings,
        provider names, titles, detail keys/values) can contain Markdown
        reserved characters without producing a 400 that silently drops the
        alert. No Markdown markers are emitted in the structural template.
        """
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"{level.value} ROBIL TRADE ALERT",
            title,
            "",
            message,
            "",
            f"⏰ {now}",
        ]

        if details:
            lines.append("")
            for k, v in details.items():
                lines.append(f"• {k}: {v}")

        return "\n".join(lines)

    async def _send_telegram(self, text: str) -> bool:
        """Send message via Telegram Bot API."""
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            # Plain text: no parse mode, so dynamic content cannot break parsing.
            "parse_mode": None,
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.error(
                    "telegram API error",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                return False
        except Exception as exc:
            logger.error("telegram send failed", error=str(exc))
            return False
