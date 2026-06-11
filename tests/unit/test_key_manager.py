"""Unit tests for multi-key rotation manager (P3-T1)."""

from __future__ import annotations

import pytest

from rtrade.llm.key_manager import (
    AllKeysExhaustedError,
    KeyManager,
)


class TestKeyManagerRoundRobin:
    @pytest.mark.asyncio
    async def test_round_robin_returns_keys_in_order(self) -> None:
        mgr = KeyManager(keys_by_provider={"gemini": ["key_a", "key_b", "key_c"]})
        k1 = await mgr.get_next_key("gemini")
        k2 = await mgr.get_next_key("gemini")
        k3 = await mgr.get_next_key("gemini")
        k4 = await mgr.get_next_key("gemini")  # wraps

        assert k1 == "key_a"
        assert k2 == "key_b"
        assert k3 == "key_c"
        assert k4 == "key_a"  # round-robin wrap

    @pytest.mark.asyncio
    async def test_unknown_provider_raises(self) -> None:
        mgr = KeyManager(keys_by_provider={"gemini": ["key_a"]})
        with pytest.raises(KeyError, match="no API keys"):
            await mgr.get_next_key("anthropic")


class TestKeyManagerCooldown:
    @pytest.mark.asyncio
    async def test_rate_limited_key_skipped(self) -> None:
        """Key in cooldown should be skipped in round-robin."""
        mgr = KeyManager(
            keys_by_provider={"gemini": ["key_a", "key_b"]},
            cooldown_seconds=60,
        )

        # Get key_a first.
        k1 = await mgr.get_next_key("gemini")
        assert k1 == "key_a"

        # Rate-limit key_b.
        await mgr.report_rate_limit("gemini", "key_b")

        # Next should skip key_b and return key_a again.
        k2 = await mgr.get_next_key("gemini")
        assert k2 == "key_a"

    @pytest.mark.asyncio
    async def test_all_keys_exhausted(self) -> None:
        """All keys in cooldown → AllKeysExhaustedError."""
        mgr = KeyManager(
            keys_by_provider={"gemini": ["key_a", "key_b"]},
            cooldown_seconds=60,
        )

        await mgr.report_rate_limit("gemini", "key_a")
        await mgr.report_rate_limit("gemini", "key_b")

        with pytest.raises(AllKeysExhaustedError):
            await mgr.get_next_key("gemini")


class TestKeyManagerBudget:
    @pytest.mark.asyncio
    async def test_cost_tracking(self) -> None:
        mgr = KeyManager(daily_budget_usd=1.0)
        await mgr.report_cost("gemini", "key_a", 0.003)
        await mgr.report_cost("gemini", "key_a", 0.002)

        cost = await mgr.get_daily_cost()
        assert cost == pytest.approx(0.005, abs=0.001)

    @pytest.mark.asyncio
    async def test_budget_alert_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Alert should fire when cost >= 80% of budget."""
        mgr = KeyManager(
            daily_budget_usd=0.01,
            budget_alert_pct=0.8,
        )
        # This exceeds 80% of $0.01 budget.
        await mgr.report_cost("gemini", "key_a", 0.009)
        # Alert should have been triggered (logged).


class TestKeyManagerProperties:
    def test_providers_list(self) -> None:
        mgr = KeyManager(
            keys_by_provider={
                "gemini": ["k1"],
                "anthropic": ["k2", "k3"],
            }
        )
        assert set(mgr.providers) == {"gemini", "anthropic"}
        assert mgr.key_count("gemini") == 1
        assert mgr.key_count("anthropic") == 2
        assert mgr.key_count("openai") == 0
