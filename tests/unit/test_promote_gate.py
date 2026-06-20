from __future__ import annotations

import pytest

from rtrade.cli.promote import (
    PromoteOutcome,
    backtest_passed,
    evaluate_promotion,
    promote_strategy,
    shadow_strategy,
)
from rtrade.persistence.models import BacktestRun


def _run(all_passed: bool) -> BacktestRun:
    # Unsaved ORM instance — no session needed for the gate logic.
    return BacktestRun(
        strategy="s3_mtf_scalper",
        instrument="XAUUSD",
        is_oos=True,
        metrics={},
        gates={"all_passed": all_passed, "per_gate": {}},
        params={},
    )


class _FakeState:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool, str | None]] = []

    async def set_state(self, strategy: str, *, enabled: bool, reason: str | None = None) -> None:
        self.calls.append((strategy, enabled, reason))

    async def is_enabled(self, strategy: str) -> bool:  # pragma: no cover - not used here
        return True


class _FakeRuns:
    def __init__(self, run: BacktestRun | None) -> None:
        self._run = run
        self.queried: list[tuple[str, str | None]] = []

    async def latest_for(self, strategy: str, instrument: str | None = None) -> BacktestRun | None:
        self.queried.append((strategy, instrument))
        return self._run


def test_backtest_passed_truth_table() -> None:
    assert backtest_passed(_run(True)) is True
    assert backtest_passed(_run(False)) is False
    assert backtest_passed(None) is False


@pytest.mark.asyncio
async def test_evaluate_promotion_scopes_by_instrument() -> None:
    runs = _FakeRuns(_run(True))
    ok, reason = await evaluate_promotion("s3_mtf_scalper", runs, instrument="XAUUSD")
    assert ok is True
    assert runs.queried == [("s3_mtf_scalper", "XAUUSD")]
    assert "pass" in reason.lower()


@pytest.mark.asyncio
async def test_promote_enables_when_latest_run_passed() -> None:
    state, runs = _FakeState(), _FakeRuns(_run(True))
    outcome = await promote_strategy(
        "s3_mtf_scalper", state_repo=state, run_repo=runs, instrument="XAUUSD"
    )
    assert isinstance(outcome, PromoteOutcome)
    assert outcome.promoted is True
    assert state.calls == [("s3_mtf_scalper", True, "promoted: backtest gate passed")]


@pytest.mark.asyncio
async def test_promote_refuses_when_latest_run_failed() -> None:
    state, runs = _FakeState(), _FakeRuns(_run(False))
    outcome = await promote_strategy("s3_mtf_scalper", state_repo=state, run_repo=runs)
    assert outcome.promoted is False
    assert state.calls == []  # never enabled on a failing gate


@pytest.mark.asyncio
async def test_promote_refuses_when_no_run_exists() -> None:
    state, runs = _FakeState(), _FakeRuns(None)
    outcome = await promote_strategy("s3_mtf_scalper", state_repo=state, run_repo=runs)
    assert outcome.promoted is False
    assert "no backtest" in outcome.reason.lower()
    assert state.calls == []


@pytest.mark.asyncio
async def test_shadow_seeds_disabled() -> None:
    state = _FakeState()
    outcome = await shadow_strategy("s4_smc_scalper", state_repo=state)
    assert outcome.promoted is False
    assert state.calls == [("s4_smc_scalper", False, "awaiting backtest validation")]
