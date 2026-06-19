"""Tests for LLM BudgetGuard (P2-4)."""

from __future__ import annotations

import pytest

from rtrade.core.config import LLMBudgetSettings
from rtrade.llm.budget_guard import BudgetGuard


@pytest.fixture
def guard() -> BudgetGuard:
    caps = LLMBudgetSettings(
        max_tokens_per_scan=1000,
        max_usd_per_day=1.0,
        max_wall_seconds_per_scan=10.0,
        max_steps_per_scan=3,
    )
    return BudgetGuard(caps)


class TestBudgetGuard:
    def test_no_stop_initially(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        assert state.budget_stop is None

    def test_tokens_cap(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        result = guard.record(state, tokens=1500)
        assert result == "tokens"

    def test_usd_day_cap(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        result = guard.record(state, usd=1.5)
        assert result == "usd_day"

    def test_steps_cap(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        guard.record(state, steps=1)
        guard.record(state, steps=1)
        result = guard.record(state, steps=2)  # total 4 > 3
        assert result == "steps"

    def test_short_circuits_after_stop(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        guard.record(state, tokens=1500)
        result = guard.record(state, tokens=1)  # should short-circuit
        assert result == "tokens"

    def test_at_80pct_daily(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        guard.record(state, usd=0.79)
        assert not guard.at_80pct_daily(state)
        guard.record(state, usd=0.02)
        assert guard.at_80pct_daily(state)

    def test_normal_flow_no_stop(self, guard: BudgetGuard) -> None:
        state = guard.start_scan()
        result = guard.record(state, tokens=100, usd=0.01, steps=1)
        assert result is None
