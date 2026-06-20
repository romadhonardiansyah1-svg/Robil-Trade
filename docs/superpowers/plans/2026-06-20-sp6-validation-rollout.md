# SP-6: Validation & Rollout (backtest gate → shadow → go-live) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the rollout of the SP-4 scalping strategies (`s3_mtf_scalper`, `s4_smc_scalper`) and the SP-5 refreshed swing strategies (`s1_trend_pullback`, `s2_range_mr`) honest and safe on XAUUSD: realistic transaction costs are applied in every backtest, and a strategy is only flipped to **enabled** in `strategy_state` after its latest `backtest_runs` row passed every go-live gate. Until it passes it stays in **shadow** (disabled → skipped by the live scan; the engine is signal-only/paper by construction). Stays type/lint/test clean.

**Architecture:** Two deterministic, unit-testable pieces plus one operator runbook.
1. **Cost realism** — `config/costs.yaml` already carries an XAUUSD entry; SP-6 pins it to a scalping-realistic spread/commission and locks it with a regression test against `backtest.costs.load_cost_models()` (the runner at `cli/backtest.py:_run` already calls `load_cost_models().get(args.symbol)` and feeds the `CostModel` into `run_walkforward_harness`, so a present XAUUSD entry = costs applied).
2. **Promote-gate** — a new `rtrade.cli.promote` subcommand. Its pure core reads the latest `backtest_runs` row for a strategy via a new `BacktestRunRepo.latest_for(...)` method and flips `StrategyStateRepo.set_state(strategy, enabled=True, ...)` only when `gates["all_passed"] is True` (the exact shape `cli/backtest.py:_run` persists). It refuses (non-zero exit) otherwise, and can seed a strategy into shadow (`enabled=False`) before validation. Mirrors `cli/backtest.py` exit-code conventions and the unified dispatch in `cli/__main__.py`.
3. **Validation matrix runbook** — operator/integration steps that run `python -m rtrade.cli.backtest` for each strategy×TF on seeded OANDA M5/M15/H4 history and require exit 0, then promote on pass. These need a live seeded DB, so they are marked **manual / integration** with exact commands + expected exit codes; only the promote-gate logic gets a deterministic unit test.

**Tech Stack:** Python 3.12 async, SQLAlchemy 2.0 async ORM (`StrategyState`, `BacktestRun`), argparse CLI, structlog, PyYAML (`costs.yaml`), pytest (mock-repo unit tests, no live DB), the existing walk-forward + DSR + PBO harness.

## Global Constraints

- Signal-only — no order/broker placement, ever. Promotion only flips a `strategy_state` flag; it never touches a broker.
- Hard risk floors (config-loader enforced, never weakened): GR-03 `rr_min ≥ 1.5`; GR-04 `sl_atr ∈ [0.5, 3.0]`; GR-05 `risk_per_trade_pct ≤ 2.0`. Not edited here; must not be disturbed.
- News blackout (GR-07) applies to ALL timeframes incl. M5/M15. Calendar fail-CLOSE: `calendar.fail_open_when_stale = false`.
- `llm.enabled = false`; GI-5: no `model_construct` on the production path.
- Warmup guarantee (P1-7): abstain (`abstain_warmup`) until a full warmup window exists, per entry timeframe.
- **`backtest.min_trades_for_validation ≥ 100` — never lowered.** The promote-gate keys off the persisted `gates.all_passed`, which already encodes the `min_trades` gate; SP-6 must not introduce a path that promotes below the floor.
- Determinism in tests: no live network, no live DB. The promote-gate unit tests use **mock `StrategyStateRepo` + `BacktestRunRepo`** (the pattern in `tests/unit/test_scan_post_llm_gate.py` and `tests/unit/test_telegram_commands.py`). Integration/manual runs skip when the stack (DB/OANDA) is unreachable.
- Toolchain via venv. Per-phase gate: `.venv\Scripts\python.exe -m ruff check src tests migrations` ; `.venv\Scripts\python.exe -m ruff format src tests migrations` ; `.venv\Scripts\python.exe -m mypy --strict src` ; `.venv\Scripts\python.exe -m pytest tests -q`.
- Commit via `COMMIT_MSG_TMP.txt` + `git commit -F COMMIT_MSG_TMP.txt`, then delete it. Before commit run `cmd /c 'if exist nul del "\\?\%CD%\nul"'`. No push unless asked. (Author does NOT run git; commit steps are documented for the executor.)
- Secrets never logged by value; reference by slot name.

---

## File Structure

- Modify: `config/costs.yaml` — pin the existing XAUUSD scalping cost entry (spread/commission/slippage).
- Modify: `src/rtrade/persistence/repositories.py` — add `BacktestRunRepo.latest_for(strategy, instrument=None) -> BacktestRun | None`.
- Create: `src/rtrade/cli/promote.py` — promote-gate core (`backtest_passed`, `evaluate_promotion`, `promote_strategy`, `shadow_strategy`) + argparse `main` with exit codes.
- Modify: `src/rtrade/cli/__main__.py` — register the `promote` subcommand in `_COMMANDS` + `_USAGE`.
- Test: `tests/unit/test_costs_xauusd.py` — `load_cost_models()` returns a `CostModel` for XAUUSD with the configured values.
- Test: `tests/unit/test_promote_gate.py` — promote-gate logic with mock repos (deterministic, no DB).
- Test: `tests/unit/test_promote_cli.py` — CLI dispatch + exit-code mapping with monkeypatched session/repos.
- Test: `tests/integration/test_backtest_run_repo_latest.py` — `latest_for` against a live DB (skips when unreachable).
- Docs: this plan's **Task 5** is the operator runbook (validation matrix commands + expected exit codes). No production code.

---

## Task 1: Pin the XAUUSD cost model + lock it with a regression test

The XAUUSD entry already exists in `config/costs.yaml` (percentage-based: `spread_pct_round_turn`, `commission_pct_round_turn`, `slippage_pct_per_side`, `pip_size: 0.01`). SP-6 keeps the schema and pins scalping-realistic values, then adds a regression test so the cost path can never silently drop to zero (a zero-cost gold scalp backtest is dishonest per design §12).

**Files:**
- Modify: `config/costs.yaml` (XAUUSD block)
- Test: `tests/unit/test_costs_xauusd.py`

**Interfaces:**
- Consumes: `rtrade.backtest.costs.load_cost_models(config_path=Path("config/costs.yaml")) -> dict[str, CostModel]`, `CostModel` (frozen dataclass with `spread_pct_rt`, `commission_pct_rt`, `slippage_pct_per_side`, `pip_size`, `total_pct_rt`).
- Produces: a present, non-zero XAUUSD `CostModel` so `cli/backtest.py:_run`'s `load_cost_models().get("XAUUSD")` is never `None`.

- [ ] **Step 1: Write the failing/locking test**

```python
# tests/unit/test_costs_xauusd.py
from __future__ import annotations

from pathlib import Path

from rtrade.backtest.costs import CostModel, load_cost_models

_COSTS = Path(__file__).resolve().parents[2] / "config" / "costs.yaml"


def test_xauusd_cost_model_present_and_nonzero() -> None:
    models = load_cost_models(_COSTS)
    assert "XAUUSD" in models, "XAUUSD must have a cost entry so gold scalps are costed"
    xau = models["XAUUSD"]
    assert isinstance(xau, CostModel)
    # Pinned scalping-realistic values (config/costs.yaml).
    assert xau.spread_pct_rt == 0.010
    assert xau.commission_pct_rt == 0.007
    assert xau.slippage_pct_per_side == 0.010
    assert xau.pip_size == 0.01


def test_xauusd_round_turn_cost_is_applied_not_zero() -> None:
    xau = load_cost_models(_COSTS)["XAUUSD"]
    # spread + commission + 2×slippage = 0.010 + 0.007 + 0.020 = 0.037 %RT.
    assert xau.total_pct_rt == 0.037
    assert xau.total_pct_rt > 0.0
```

- [ ] **Step 2: Run test to verify current state**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_costs_xauusd.py -q`
Expected: FAIL if the on-disk values differ from the pinned ones (e.g. `assert xau.total_pct_rt == 0.037`). If the repo's current values already match, this test PASSES immediately and acts purely as a regression lock — that is acceptable for this task (note it in the commit body).

- [ ] **Step 3: Pin the XAUUSD block in `config/costs.yaml`**

Ensure the XAUUSD block reads exactly (keep the other instruments untouched):

```yaml
costs:
  XAUUSD:
    spread_pct_round_turn: 0.010      # ~0.26-0.35 USD/oz round-turn at ~2600
    commission_pct_round_turn: 0.007  # ~$7/lot round-turn
    slippage_pct_per_side: 0.010      # gold scalping slippage, per side
    pip_size: 0.01
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_costs_xauusd.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Gate + commit**

```
.venv\Scripts\python.exe -m ruff check src tests ; .venv\Scripts\python.exe -m ruff format src tests ; .venv\Scripts\python.exe -m mypy --strict src ; .venv\Scripts\python.exe -m pytest tests -q
```
Expected: ruff clean, mypy `Success`, all green.

```
git add config/costs.yaml tests/unit/test_costs_xauusd.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp6): pin XAUUSD scalping cost model + regression-lock load_cost_models`

---

## Task 2: `BacktestRunRepo.latest_for()` — newest run for one strategy

The promote-gate needs the **latest** `backtest_runs` row for a given strategy (optionally scoped to an instrument). The repo currently exposes only `add(...)` and `recent(limit)` (newest overall, not per-strategy), so add a focused, typed query.

**Files:**
- Modify: `src/rtrade/persistence/repositories.py` (the `BacktestRunRepo` class)
- Test: `tests/integration/test_backtest_run_repo_latest.py` (skips without DB)

**Interfaces:**
- Consumes: existing `select`, `BacktestRun`, `AsyncSession`.
- Produces: `async def latest_for(self, strategy: str, instrument: str | None = None) -> BacktestRun | None` — newest row (`ORDER BY id DESC LIMIT 1`) filtered by `strategy` and, when given, `instrument`. Returns `None` when no run exists.

- [ ] **Step 1: Add the method to `BacktestRunRepo`**

In `src/rtrade/persistence/repositories.py`, inside `class BacktestRunRepo`, add after `recent`:

```python
    async def latest_for(
        self, strategy: str, instrument: str | None = None
    ) -> BacktestRun | None:
        """Newest run for one strategy (optionally one instrument), or None.

        The promote-gate keys go-live off this row's ``gates['all_passed']``.
        """
        stmt = select(BacktestRun).where(BacktestRun.strategy == strategy)
        if instrument is not None:
            stmt = stmt.where(BacktestRun.instrument == instrument)
        stmt = stmt.order_by(BacktestRun.id.desc()).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
```

- [ ] **Step 2: Write the integration test (skips without DB)**

```python
# tests/integration/test_backtest_run_repo_latest.py
from __future__ import annotations

import os
from datetime import date

import pytest

from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.repositories import BacktestRunRepo

pytestmark = pytest.mark.integration


def _db_url() -> str | None:
    return os.environ.get("RTRADE_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.mark.asyncio
async def test_latest_for_returns_newest_run_per_strategy() -> None:
    url = _db_url()
    if not url:
        pytest.skip("no DATABASE_URL — live BacktestRunRepo test skipped")
    engine = _get_engine(url)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        repo = BacktestRunRepo(session)
        await repo.add(
            strategy="s_probe", instrument="XAUUSD",
            window_start=date(2025, 1, 1), window_end=date(2025, 6, 1),
            is_oos=True, metrics={}, gates={"all_passed": False}, params={},
        )
        newest = await repo.add(
            strategy="s_probe", instrument="XAUUSD",
            window_start=date(2025, 6, 1), window_end=date(2026, 1, 1),
            is_oos=True, metrics={}, gates={"all_passed": True}, params={},
        )
        await session.flush()
        found = await repo.latest_for("s_probe", "XAUUSD")
        assert found is not None
        assert found.id == newest.id
        assert found.gates == {"all_passed": True}
        assert await repo.latest_for("does_not_exist") is None
        await session.rollback()  # leave no test rows behind
```

- [ ] **Step 3: Run it to confirm it SKIPS cleanly (no DB in this env)**

Run: `.venv\Scripts\python.exe -m pytest tests/integration/test_backtest_run_repo_latest.py -q`
Expected: `1 skipped` (skip reason: no DATABASE_URL). The deterministic coverage of the gate logic lives in Task 3 with mock repos.

- [ ] **Step 4: Gate + commit**

Run the full gate (`ruff`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: all green, integration test skipped.

```
git add src/rtrade/persistence/repositories.py tests/integration/test_backtest_run_repo_latest.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp6): BacktestRunRepo.latest_for() — newest gate run per strategy`

---

## Task 3: Promote-gate core logic (`rtrade.cli.promote`)

The deterministic heart of SP-6. Pure async functions that take repo objects (duck-typed via `Protocol`) so the unit test injects mocks — no live DB. `promote_strategy` flips `enabled=True` ONLY when the latest run passed; `shadow_strategy` seeds `enabled=False` (needed because `StrategyStateRepo.is_enabled` defaults a *missing* row to `True` — see Self-Review judgment calls).

**Files:**
- Create: `src/rtrade/cli/promote.py`
- Test: `tests/unit/test_promote_gate.py`

**Interfaces:**
- Consumes: `BacktestRun`, `STRATEGY_REGISTRY`, the persisted gate shape `gates={"all_passed": bool, "per_gate": {...}}`.
- Produces:
  - `@dataclass(frozen=True, slots=True) class PromoteOutcome: strategy: str; promoted: bool; reason: str`
  - `def backtest_passed(run: BacktestRun | None) -> bool`
  - `async def evaluate_promotion(strategy: str, run_repo: _RunRepo, *, instrument: str | None = None) -> tuple[bool, str]`
  - `async def promote_strategy(strategy: str, *, state_repo: _StateRepo, run_repo: _RunRepo, instrument: str | None = None) -> PromoteOutcome`
  - `async def shadow_strategy(strategy: str, *, state_repo: _StateRepo, reason: str = "awaiting backtest validation") -> PromoteOutcome`

- [ ] **Step 1: Write the failing test (mock repos, no DB)**

```python
# tests/unit/test_promote_gate.py
from __future__ import annotations

from typing import Any

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
        strategy="s3_mtf_scalper", instrument="XAUUSD",
        is_oos=True, metrics={}, gates={"all_passed": all_passed, "per_gate": {}}, params={},
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_promote_gate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rtrade.cli.promote'`.

- [ ] **Step 3: Implement the promote-gate module**

```python
# src/rtrade/cli/promote.py
"""Go-live promotion gate (SP-6, FR-BT-05).

Flip a strategy's ``strategy_state`` row to enabled ONLY when its latest
``backtest_runs`` row passed every go-live gate (the ``gates['all_passed']``
flag persisted by ``rtrade.cli.backtest``). Refuses (non-zero exit) otherwise.
Also seeds a strategy into shadow (``enabled=False``) before it has been
validated, because ``StrategyStateRepo.is_enabled`` defaults a *missing* row to
True (default-enabled) and the live scan SKIPS disabled strategies.

Signal-only: promotion flips a flag in Postgres; it never touches a broker.

Usage:
    python -m rtrade.cli.promote --strategy s3_mtf_scalper --symbol XAUUSD
    python -m rtrade.cli.promote --strategy s3_mtf_scalper --shadow

Exit codes (mirror rtrade.cli.backtest):
    0  enabled (latest backtest passed) — or --shadow seed applied
    1  refused — latest backtest for the strategy did not pass all gates
    2  operational error — unknown strategy / no backtest run found / bad args
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import sys
from typing import Protocol

import structlog

from rtrade.core.config import AppConfig
from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.models import BacktestRun
from rtrade.persistence.repositories import BacktestRunRepo, StrategyStateRepo
from rtrade.strategies import STRATEGY_REGISTRY

logger = structlog.get_logger(__name__)


class _StateRepo(Protocol):
    async def set_state(
        self, strategy: str, *, enabled: bool, reason: str | None = None
    ) -> None: ...


class _RunRepo(Protocol):
    async def latest_for(
        self, strategy: str, instrument: str | None = None
    ) -> BacktestRun | None: ...


@dataclass(frozen=True, slots=True)
class PromoteOutcome:
    """Result of a promote/shadow decision (exit code derived from this)."""

    strategy: str
    promoted: bool
    reason: str


def backtest_passed(run: BacktestRun | None) -> bool:
    """True iff a run exists and its persisted gates report all_passed."""
    if run is None or not isinstance(run.gates, dict):
        return False
    return run.gates.get("all_passed") is True


async def evaluate_promotion(
    strategy: str, run_repo: _RunRepo, *, instrument: str | None = None
) -> tuple[bool, str]:
    """Read the latest backtest run and decide if it clears the go-live gate."""
    run = await run_repo.latest_for(strategy, instrument)
    if run is None:
        scope = f" for {instrument}" if instrument else ""
        return False, f"no backtest run found for {strategy}{scope}"
    if not backtest_passed(run):
        gates = run.gates if isinstance(run.gates, dict) else {}
        return False, f"latest backtest did not pass all gates (gates={gates})"
    return True, "promoted: backtest gate passed"


async def promote_strategy(
    strategy: str,
    *,
    state_repo: _StateRepo,
    run_repo: _RunRepo,
    instrument: str | None = None,
) -> PromoteOutcome:
    """Enable a strategy iff its latest backtest passed; never enable otherwise."""
    ok, reason = await evaluate_promotion(strategy, run_repo, instrument=instrument)
    if ok:
        await state_repo.set_state(strategy, enabled=True, reason="promoted: backtest gate passed")
        logger.info("strategy promoted to live", strategy=strategy, instrument=instrument)
    else:
        logger.warning("promotion refused", strategy=strategy, reason=reason)
    return PromoteOutcome(strategy=strategy, promoted=ok, reason=reason)


async def shadow_strategy(
    strategy: str,
    *,
    state_repo: _StateRepo,
    reason: str = "awaiting backtest validation",
) -> PromoteOutcome:
    """Seed a strategy into shadow (disabled) so the live scan skips it."""
    await state_repo.set_state(strategy, enabled=False, reason=reason)
    logger.info("strategy set to shadow (disabled)", strategy=strategy, reason=reason)
    return PromoteOutcome(strategy=strategy, promoted=False, reason=f"shadow: {reason}")


def _out(line: str) -> None:
    print(line)  # noqa: T201 - CLI report is the user-facing output


def _err(line: str) -> None:
    print(line, file=sys.stderr)  # noqa: T201 - CLI error channel


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="rtrade promote")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", dest="symbol", default=None)
    ap.add_argument(
        "--shadow",
        action="store_true",
        help="seed the strategy into shadow (disabled) instead of promoting",
    )
    return ap.parse_args(argv)


async def _run(args: argparse.Namespace, cfg: AppConfig) -> int:
    if args.strategy not in STRATEGY_REGISTRY:
        _err(f"ERROR: unknown strategy {args.strategy}")
        return 2

    engine = _get_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        state_repo = StrategyStateRepo(session)
        if args.shadow:
            outcome = await shadow_strategy(args.strategy, state_repo=state_repo)
            await session.commit()
            _out(f"SHADOW: {outcome.strategy} disabled ({outcome.reason})")
            return 0

        run_repo = BacktestRunRepo(session)
        outcome = await promote_strategy(
            args.strategy, state_repo=state_repo, run_repo=run_repo, instrument=args.symbol
        )
        if outcome.promoted:
            await session.commit()
            _out(f"PROMOTED: {outcome.strategy} enabled ({outcome.reason})")
            return 0
        # Refusal: nothing was written. Distinguish "no run" (operational, 2)
        # from "ran but failed the gate" (gate refusal, 1).
        await session.rollback()
        _err(f"REFUSED: {outcome.strategy} — {outcome.reason}")
        return 2 if outcome.reason.startswith("no backtest run") else 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    cfg = AppConfig.load()
    raise SystemExit(asyncio.run(_run(args, cfg)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_promote_gate.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Gate + commit**

Run the full gate (`ruff`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green.

```
git add src/rtrade/cli/promote.py tests/unit/test_promote_gate.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp6): promote-gate core (enable on passing backtest, shadow-seed otherwise)`

---

## Task 4: Register `promote` subcommand + CLI exit-code mapping

Wire `rtrade.cli.promote` into the unified dispatch (`cli/__main__.py`) and lock the exit-code mapping (0 promoted / 1 gate-fail / 2 no-run-or-unknown) with a CLI test that monkeypatches the session + repos (no DB), mirroring `tests/unit/test_telegram_commands.py`.

**Files:**
- Modify: `src/rtrade/cli/__main__.py` (add `promote` to `_COMMANDS` + `_USAGE`)
- Test: `tests/unit/test_promote_cli.py`

**Interfaces:**
- Consumes: `rtrade.cli.promote.main`, the existing `_COMMANDS` dispatch table.
- Produces: `rtrade promote ...` routes to `promote.main`; `promote.main(argv)` raises `SystemExit` with the gate code.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_promote_cli.py
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import rtrade.cli.promote as promote_mod
from rtrade.persistence.models import BacktestRun


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


def _run(all_passed: bool | None) -> BacktestRun | None:
    if all_passed is None:
        return None
    return BacktestRun(
        strategy="s3_mtf_scalper", instrument="XAUUSD",
        is_oos=True, metrics={}, gates={"all_passed": all_passed}, params={},
    )


def _wire(monkeypatch: pytest.MonkeyPatch, *, run: BacktestRun | None) -> _FakeSession:
    session = _FakeSession()
    monkeypatch.setattr(promote_mod, "_get_engine", lambda _url: object())
    monkeypatch.setattr(promote_mod, "create_session_factory", lambda _e: _FakeSessionFactory(session))

    class _State:
        def __init__(self, _s: Any) -> None:
            pass

        async def set_state(self, *_a: Any, **_k: Any) -> None:
            return None

    class _Runs:
        def __init__(self, _s: Any) -> None:
            pass

        async def latest_for(self, _strategy: str, _instrument: str | None = None) -> BacktestRun | None:
            return run

    monkeypatch.setattr(promote_mod, "StrategyStateRepo", _State)
    monkeypatch.setattr(promote_mod, "BacktestRunRepo", _Runs)
    monkeypatch.setattr(
        promote_mod.AppConfig, "load",
        classmethod(lambda _cls: SimpleNamespace(secrets=SimpleNamespace(database_url="x"))),
    )
    return session


def test_unknown_strategy_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "not_real", "--symbol", "XAUUSD"])
    assert exc.value.code == 2


def test_passing_run_promotes_exit_0(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _wire(monkeypatch, run=_run(True))
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 0
    assert session.committed is True


def test_failing_run_refuses_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=_run(False))
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 1


def test_no_run_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--symbol", "XAUUSD"])
    assert exc.value.code == 2


def test_shadow_exits_0(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _wire(monkeypatch, run=None)
    with pytest.raises(SystemExit) as exc:
        promote_mod.main(["--strategy", "s3_mtf_scalper", "--shadow"])
    assert exc.value.code == 0
    assert session.committed is True
```

NOTE: `test_unknown_strategy_exits_2` validates the strategy name before opening a session, so the unknown-strategy branch returns 2 even though no run is wired. Keep the `STRATEGY_REGISTRY` membership check as the first line of `_run` (Task 3) so this holds.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_promote_cli.py -q`
Expected: FAIL — `promote` not yet dispatchable / assertions on a not-yet-registered command (the module import works from Task 3, but the dispatch wiring assertion below needs Step 3).

- [ ] **Step 3: Register the subcommand in `cli/__main__.py`**

Add the runner and table entry. After `_run_backtest`:

```python
def _run_promote() -> None:
    # `promote.main()` parses sys.argv via argparse and raises SystemExit with
    # the go-live promotion code (0 enabled / 1 gate-fail / 2 no-run-or-unknown).
    from rtrade.cli.promote import main as promote_main

    promote_main()
```

Add to `_COMMANDS`:

```python
_COMMANDS: dict[str, Callable[[], None]] = {
    "auth": _run_auth,
    "backfill": _run_backfill,
    "backtest": _run_backtest,
    "promote": _run_promote,
    "bot": _run_bot,
}
```

Add to `_USAGE` (under the `backtest` line):

```
  promote   Flip a strategy to live after its backtest passes (shadow→live gate)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/unit/test_promote_cli.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Gate + commit**

Run the full gate (`ruff`, `ruff format`, `mypy --strict src`, `pytest tests -q`). Expected: clean, all green.

```
git add src/rtrade/cli/__main__.py tests/unit/test_promote_cli.py
git commit -F COMMIT_MSG_TMP.txt
```
Message: `feat(sp6): register rtrade promote subcommand + lock exit-code mapping`

---

## Task 5: Validation matrix runbook (manual / integration) + shadow note

This task is an **operator runbook**, not production code. It needs a live seeded Postgres with OANDA M5/M15/H4 history (from SP-1's backfill), so it is run manually / as integration and gated on **exit codes**, not a unit test. The promote-gate logic that this runbook leans on is already unit-tested deterministically (Tasks 3–4).

**Gate thresholds (config-driven, `config/settings.yaml backtest.gates`; never lowered):**
`min_trades_for_validation: 100` · `oos_expectancy_after_costs: "> 0"` · `oos_profit_factor: ">= 1.15"` · `deflated_sharpe_prob: ">= 0.90"` · `pbo_max: 0.30` · `max_drawdown_pct: 25`. Walk-forward: `train_months: 12`, `test_months: 6`, `step_months: 3`.

### 5.0 Prerequisites (run once)
- SP-1 merged (OANDA provider + multi-account) and SP-2 entry TFs (M5/M15) enabled for XAUUSD in `config/instruments.yaml`.
- SP-4 (`s3_mtf_scalper`, `s4_smc_scalper` registered in `STRATEGY_REGISTRY`) and SP-5 (refreshed `s1_trend_pullback`, `s2_range_mr`) merged.
- Backfill seeded (paginated OANDA history; covers the windows below):
  ```
  python -m rtrade.cli.backfill --symbol XAUUSD --tf 5m  --from 2023-01-01 --to 2026-06-01
  python -m rtrade.cli.backfill --symbol XAUUSD --tf 15m --from 2023-01-01 --to 2026-06-01
  python -m rtrade.cli.backfill --symbol XAUUSD --tf 4h  --from 2021-01-01 --to 2026-06-01
  python -m rtrade.cli.backfill --symbol XAUUSD --tf 1h  --from 2021-01-01 --to 2026-06-01
  ```

### 5.1 Seed every candidate strategy into SHADOW first
`StrategyStateRepo.is_enabled` returns `True` for a *missing* row, so a freshly-registered strategy would otherwise run live immediately. Seed all four disabled before any backtest:
```
python -m rtrade.cli.promote --strategy s3_mtf_scalper --shadow   # expect exit 0
python -m rtrade.cli.promote --strategy s4_smc_scalper --shadow   # expect exit 0
python -m rtrade.cli.promote --strategy s1_trend_pullback --shadow # expect exit 0
python -m rtrade.cli.promote --strategy s2_range_mr --shadow      # expect exit 0
```
After this, the live scan in `pipeline/scan.py:_run_strategies` hits `if not await state_repo.is_enabled(strategy_name): continue` and **skips** these strategies — no live signals, no trust.

### 5.2 Run the validation matrix (each must exit 0)
Scalpers on the SP-2 entry TFs; refreshed swing strategies on their swing TFs. Each command runs walk-forward + DSR + PBO + cost-adjusted gates and persists a `backtest_runs` row (`gates.all_passed`).

| # | Command | Expected exit |
|---|---------|---------------|
| 1 | `python -m rtrade.cli.backtest --strategy s3_mtf_scalper --symbol XAUUSD --tf 5m  --from 2023-01-01 --to 2026-06-01` | `0` |
| 2 | `python -m rtrade.cli.backtest --strategy s3_mtf_scalper --symbol XAUUSD --tf 15m --from 2023-01-01 --to 2026-06-01` | `0` |
| 3 | `python -m rtrade.cli.backtest --strategy s4_smc_scalper --symbol XAUUSD --tf 5m  --from 2023-01-01 --to 2026-06-01` | `0` |
| 4 | `python -m rtrade.cli.backtest --strategy s4_smc_scalper --symbol XAUUSD --tf 15m --from 2023-01-01 --to 2026-06-01` | `0` |
| 5 | `python -m rtrade.cli.backtest --strategy s1_trend_pullback --symbol XAUUSD --tf 4h --from 2021-01-01 --to 2026-06-01` | `0` |
| 6 | `python -m rtrade.cli.backtest --strategy s2_range_mr --symbol XAUUSD --tf 4h --from 2021-01-01 --to 2026-06-01` | `0` |

Exit-code meaning (`cli/backtest.py`): `0` = all gates passed; `1` = ran but a gate failed (keep in shadow, iterate the strategy); `2` = operational (instrument missing / insufficient candles `< min_trades_for_validation + 250` warmup / unknown strategy → fix data or name, re-run). A non-zero exit here is the gate **correctly** refusing a weak gold-scalp edge after real costs (design §11/§12) — do NOT lower thresholds.

### 5.3 Promote ONLY the strategies that passed
For every strategy whose backtest exited 0, flip it live (the promote-gate re-reads the latest `backtest_runs` row and refuses if it did not pass — belt and suspenders):
```
python -m rtrade.cli.promote --strategy s3_mtf_scalper --symbol XAUUSD     # exit 0 only if latest run passed
python -m rtrade.cli.promote --strategy s4_smc_scalper --symbol XAUUSD     # exit 0 only if latest run passed
python -m rtrade.cli.promote --strategy s1_trend_pullback --symbol XAUUSD  # exit 0 only if latest run passed
python -m rtrade.cli.promote --strategy s2_range_mr --symbol XAUUSD        # exit 0 only if latest run passed
```
Expected: `0` (enabled) when the latest run passed; `1` (refused) when it failed; `2` when no run exists for that strategy. A strategy left disabled stays in shadow indefinitely until a future passing run + promote.

### 5.4 Shadow / paper-tracking note (verified against the real code)
- **Disabled = skipped, not separately shadow-published.** `pipeline/scan.py:_run_strategies` runs the F2 check `if not await state_repo.is_enabled(strategy_name): logger.info("strategy disabled, skipping"); continue` — a disabled strategy produces no candidates and no signals. So "shadow" here means *the strategy is off the live scan path* until promoted.
- **The whole engine is already paper/signal-only.** There is no broker. Once a strategy is enabled and publishes a `Signal`, the existing paper-tracking path replays it: `pipeline/scan.py:track_paper_signals()` iterates `SignalRepo.open_for_tracking()` (statuses `PUBLISHED`/`FILLED`), advances fills/SL/TP/expiry against later candles (`papertrack.tracker.replay_signal`, `papertrack.virtual_exits`, `papertrack.minute_resolution`), and writes `outcome_r` back via `SignalRepo.update_tracking_status`. It is scheduled every 15 min by `scheduler/jobs.py:paper_track_job` / `scheduler/main.py` (`paper_track` job). This is what records and tracks signals "without being trusted" (no order placement).
- **Auto-demote is already wired (GR-13).** `_run_strategies` calls `state_repo.set_state(candidate.strategy, enabled=False, ...)` when the expectancy guard (GR-13) trips, so a promoted strategy that degrades on paper outcomes is automatically returned to shadow — complementary to this plan's promote-on-pass direction.

### 5.5 Record the rollout
Capture each command's exit code and the printed report (`cli/backtest.py:_print_report` prints OOS trades / expectancy / PF / DD / DSR / PBO and per-gate PASS/FAIL) in the rollout log. No code change; no commit unless the runbook itself is added to `docs/`.

---

## Self-Review (completed by plan author)

**1. Spec coverage (SP-6 / §11 / §12 / §13):**
- XAUUSD realistic cost model applied in backtests (§12) → Task 1 (pin + regression lock; entry already existed). ✅
- Go-live gate: enable only after a passing backtest, else shadow (§11) → Tasks 2–4 (`latest_for` + `promote_strategy`/`shadow_strategy` + CLI). ✅
- Validation matrix {s3,s4}×{5m,15m} + refreshed {s1,s2}, exit-0 required, gates ≥100 trades / OOS expectancy>0 / PF≥1.15 / DSR≥0.90 / PBO≤0.30 / DD≤25% (§11) → Task 5 runbook. ✅
- Shadow/paper path references the real `track_paper_signals` + `is_enabled` gating + GR-13 auto-demote (§13) → Task 5.4. ✅
- Determinism: only the promote-gate logic is unit-tested (mock repos); live backtests + `latest_for` are integration/manual with explicit skips → Tasks 2,3,4 vs 5. ✅

**2. Hard-floor / safety compliance:** `min_trades_for_validation ≥ 100` never lowered; promote keys off persisted `gates.all_passed` (which encodes every floor); signal-only (promotion flips a DB flag, never a broker); `llm.enabled` untouched. ✅

**3. Placeholder scan:** No TBD/TODO; every code step has complete, typed code, exact paths, and exact commands/exit codes.

**4. Type / interface consistency:** `BacktestRunRepo.latest_for(strategy, instrument=None) -> BacktestRun | None` identical in Tasks 2, 3, 4. `promote_strategy(strategy, *, state_repo, run_repo, instrument=None) -> PromoteOutcome` and `shadow_strategy(strategy, *, state_repo, reason=...) -> PromoteOutcome` consistent across Task 3 impl + Task 3/4 tests. Gate shape `gates={"all_passed": bool, ...}` matches exactly what `cli/backtest.py:_run` persists. Exit codes (0/1/2) match `cli/backtest.py` conventions.

**5. Judgment calls (flagged for reviewer):**
- *XAUUSD cost entry already exists* — the task said "add"; the repo already has a percentage-based XAUUSD block. SP-6 therefore **pins + regression-locks** it rather than adding a duplicate, keeping the existing schema (`spread_pct_round_turn`/`commission_pct_round_turn`/`slippage_pct_per_side`/`pip_size`). If the on-disk values already equal the pinned ones, Task 1's test passes immediately as a pure lock.
- *"Shadow/paper" mapping* — there is **no separate shadow-publish mode**: `is_enabled() == False` makes `_run_strategies` skip the strategy entirely (no signals). The system is paper/signal-only by nature (no broker; `track_paper_signals` replays published signals). So the rollout adds an explicit **shadow-seed** step (`--shadow` → `set_state(enabled=False)`) because `is_enabled()` defaults a *missing* row to enabled — without seeding, a newly-registered SP-4/SP-5 strategy would go live immediately.
- *No-run vs failed-run exit codes* — promote returns `2` when no `backtest_runs` row exists (operational) and `1` when a row exists but failed the gate (refusal), mirroring `cli/backtest.py`'s `2` (operational) vs `1` (gate fail).
- *Swing TF choice* — `s1`/`s2` are validated on `4h` (their refreshed swing entry TF). Operators may additionally validate `1h`; the matrix lists `4h` as the required gate.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-20-sp6-validation-rollout.md`.
This is **SP-6 of 6** (final gate; depends on SP-4 + SP-5 being merged). Recommended execution: **subagent-driven-development** (fresh subagent per task + two-stage review), one task at a time, full gate green before advancing. Tasks 1–4 are deterministic code (unit/integration tests); Task 5 is the operator runbook run against a live seeded stack and is gated on exit codes, not unit tests.
