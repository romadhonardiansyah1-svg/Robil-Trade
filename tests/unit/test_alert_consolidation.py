"""P2-5 (A6): live failure alerts route through AlertManager dedup.

Verifies that when jobs._alert_manager is wired, _send_failure_alert with an
alert_type routes through the AlertManager (so per-type cooldown dedup applies),
and that a repeated same-type alert within the cooldown window is suppressed.
Deterministic, no network: AlertManager._send_telegram is replaced by a spy.
"""

from __future__ import annotations

import pytest

from rtrade.monitoring.alerts import AlertManager, AlertType
from rtrade.scheduler import jobs


@pytest.fixture(autouse=True)
def _reset_manager() -> None:
    jobs._alert_manager = None


@pytest.mark.asyncio
async def test_typed_alert_routes_through_manager_and_dedups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    async def fake_send_telegram(self: AlertManager, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(AlertManager, "_send_telegram", fake_send_telegram)

    manager = AlertManager("token", "chat", enabled=True)
    jobs._alert_manager = manager

    # First typed alert is delivered through the manager.
    await jobs._send_failure_alert("scan boom", alert_type=AlertType.SCAN_FAILED)
    assert len(sent) == 1
    assert "scan boom" in sent[0]

    # Same-type alert within cooldown is suppressed (dedup).
    await jobs._send_failure_alert("scan boom again", alert_type=AlertType.SCAN_FAILED)
    assert len(sent) == 1

    # A different alert type is not suppressed by the first type's cooldown.
    await jobs._send_failure_alert("provider gone", alert_type=AlertType.PROVIDER_DOWN)
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_no_alert_type_uses_direct_fallback_not_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Message-only calls (e.g. scan_job) bypass the manager → direct fallback."""
    sent: list[str] = []

    async def fake_send_telegram(self: AlertManager, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(AlertManager, "_send_telegram", fake_send_telegram)
    jobs._alert_manager = AlertManager("token", "chat", enabled=True)

    # Force a creds-less config so the direct fallback short-circuits (no network).
    class _Secrets:
        telegram_bot_token = ""
        telegram_chat_id = ""

    class _Cfg:
        secrets = _Secrets()

    monkeypatch.setattr(jobs.AppConfig, "load", classmethod(lambda cls, *a, **k: _Cfg()))

    # No alert_type → must NOT route through the manager even when one is wired.
    await jobs._send_failure_alert("untyped message")
    assert sent == []
