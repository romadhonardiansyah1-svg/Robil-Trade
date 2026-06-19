from __future__ import annotations

from typing import Any

import pytest

from rtrade.core.errors import RateLimitExceeded
from rtrade.scheduler import jobs


@pytest.fixture(autouse=True)
def _reset_job_state() -> None:
    jobs._fail_counts.clear()
    jobs._last_alert_at.clear()


@pytest.mark.asyncio
async def test_scan_job_suppresses_rate_limit_telegram_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_scan(*args: Any, **kwargs: Any) -> None:
        raise RateLimitExceeded("TwelveData 429: rate limit hit")

    alerts: list[str] = []

    async def collect_alert(message: str) -> None:
        alerts.append(message)

    monkeypatch.setattr(jobs, "run_scan", fail_scan)
    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)

    for _ in range(4):
        await jobs.scan_job("USDJPY", "1h")

    assert alerts == []
    assert jobs._fail_counts["USDJPY:1h"] == 4


@pytest.mark.asyncio
async def test_scan_job_alerts_non_rate_limit_once_until_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_scan(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("database unavailable")

    alerts: list[str] = []

    async def collect_alert(message: str) -> None:
        alerts.append(message)

    monkeypatch.setattr(jobs, "run_scan", fail_scan)
    monkeypatch.setattr(jobs, "_send_failure_alert", collect_alert)

    for _ in range(4):
        await jobs.scan_job("USDJPY", "1h")

    assert len(alerts) == 1
    assert "database unavailable" in alerts[0]
