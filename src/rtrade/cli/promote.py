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
