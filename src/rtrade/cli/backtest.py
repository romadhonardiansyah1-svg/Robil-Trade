"""Go-live statistical gate runner (FR-BT-01..05, G-04, ADR-A07).

Load a candle range from Postgres -> walk-forward harness -> validation gates
(thresholds from settings.yaml) -> print report -> persist to ``backtest_runs``
-> exit 0 when every gate passes, non-zero otherwise.

Usage:
    python -m rtrade.cli.backtest --strategy s1_trend_pullback --symbol XAUUSD \
        --tf 1h --from 2025-01-01 --to 2026-01-01

The runner is signal-only and read-mostly: it never places orders. The exit
code is the go-live gate — CI / operators key promotion off a zero exit.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import re
import sys

import pandas as pd
import structlog
import yaml

from rtrade.backtest.costs import get_cost_model
from rtrade.backtest.harness import run_walkforward_harness
from rtrade.backtest.metrics import BacktestMetrics
from rtrade.backtest.validation import ValidationGateResult, run_validation_gates
from rtrade.core.config import AppConfig
from rtrade.core.constants import Timeframe
from rtrade.core.errors import ConfigError
from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.models import Candle
from rtrade.persistence.repositories import BacktestRunRepo, CandleRepo, InstrumentRepo
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig

logger = structlog.get_logger(__name__)

# Number of warmup bars the harness needs in front of the first test window for
# indicators to be warm (matches harness.run_walkforward_harness default).
_WARMUP_BARS = 250


def parse_gate_expr(s: str | float | int) -> float:
    """Parse a settings.yaml gate threshold into a number.

    Accepts numeric values as-is and comparison strings like ">= 0.90",
    "> 0", "<= -1.5" by extracting the (optionally signed) number.
    """
    if isinstance(s, bool):  # guard: bool is an int subclass
        raise ValueError(f"gate threshold must be numeric, got bool {s!r}")
    if isinstance(s, (int, float)):
        return float(s)
    match = re.search(r"-?\d+\.?\d*", str(s))
    if match is None:
        raise ValueError(f"cannot parse threshold from {s!r}")
    return float(match.group())


def _load_strategy_config(strategy_name: str) -> StrategyConfig:
    """Load ``config/strategies/<name>.yaml`` (mirrors the scan loader)."""
    path = Path("config") / "strategies" / f"{strategy_name}.yaml"
    with path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ConfigError(f"strategy config must be a mapping: {path}")
    return StrategyConfig(raw=doc)


def _build_df(candles: list[Candle]) -> pd.DataFrame:
    """Turn ORM candles into the OHLCV frame the harness expects (ts-indexed)."""
    df = pd.DataFrame(
        [
            {
                "ts": c.ts,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in candles
        ]
    )
    return df.set_index("ts").sort_index()


def _evaluate(
    cfg: AppConfig, metrics: BacktestMetrics, *, permutation_p: float | None
) -> ValidationGateResult:
    """Run validation gates with thresholds pulled from settings.yaml.

    Single config per run, so ``n_trials=1`` (honest — never inflated to game
    the deflated-Sharpe gate).
    """
    bt = cfg.settings.backtest
    gates = bt.gates
    return run_validation_gates(
        metrics,
        n_trials=1,
        min_trades=bt.min_trades_for_validation,
        min_expectancy=parse_gate_expr(gates.oos_expectancy_after_costs),
        min_profit_factor=parse_gate_expr(gates.oos_profit_factor),
        max_drawdown_pct=float(gates.max_drawdown_pct),
        min_dsr_prob=parse_gate_expr(gates.deflated_sharpe_prob),
        max_pbo=float(gates.pbo_max),
        permutation_p=permutation_p,
    )


def _out(line: str) -> None:
    print(line)  # noqa: T201 - CLI report is the user-facing output


def _err(line: str) -> None:
    print(line, file=sys.stderr)  # noqa: T201 - CLI error channel


def _print_report(
    args: argparse.Namespace, vgr: ValidationGateResult, permutation_p: float | None
) -> None:
    _out(f"=== Backtest {args.strategy} / {args.symbol} {args.tf} ===")
    _out(f"OOS trades:    {vgr.n_trades_oos}")
    _out(f"Expectancy:    {vgr.expectancy_oos:.4f} R")
    _out(f"Profit factor: {vgr.profit_factor_oos:.2f}")
    _out(f"Max DD:        {vgr.max_drawdown_pct:.2f}%")
    _out(f"DSR prob:      {vgr.dsr_probability:.4f}")
    _out(f"PBO:           {vgr.pbo:.4f}")
    if permutation_p is not None:
        _out(f"Permutation p: {permutation_p:.4f}")
    for gate_id, passed in vgr.gate_results.items():
        _out(f"  [{'PASS' if passed else 'FAIL'}] {gate_id}")
    _out(f"\nALL PASSED: {vgr.all_passed}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="rtrade backtest")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--from", dest="from_date", required=True, type=date.fromisoformat)
    ap.add_argument("--to", dest="to_date", required=True, type=date.fromisoformat)
    ap.add_argument("--baseline-strategy", dest="baseline_strategy", default=None)
    return ap.parse_args(argv)


async def _run(args: argparse.Namespace, cfg: AppConfig) -> int:
    tf = Timeframe(args.tf)
    engine = _get_engine(cfg.secrets.database_url)
    session_factory = create_session_factory(engine)

    # [start, end) on bar OPEN time; +1 day makes the --to date inclusive.
    start_dt = datetime.combine(args.from_date, time.min, tzinfo=UTC)
    end_dt = datetime.combine(args.to_date + timedelta(days=1), time.min, tzinfo=UTC)

    async with session_factory() as session:
        inst = await InstrumentRepo(session).get_by_symbol(args.symbol)
        if inst is None:
            _err(f"ERROR: instrument {args.symbol} not found")
            return 2
        candles = await CandleRepo(session).get_range(inst.id, tf, start_dt, end_dt)

    min_needed = cfg.settings.backtest.min_trades_for_validation + _WARMUP_BARS
    if len(candles) < min_needed:
        _err(f"ERROR: insufficient candles ({len(candles)} < {min_needed})")
        return 2

    strategy_cls = STRATEGY_REGISTRY.get(args.strategy)
    if strategy_cls is None:
        _err(f"ERROR: unknown strategy {args.strategy}")
        return 2

    df = _build_df(candles)
    strategy = strategy_cls()
    strategy_cfg = _load_strategy_config(args.strategy)
    try:
        cost_model = get_cost_model(args.symbol)
    except ConfigError as exc:
        _err(f"ERROR: {exc}")
        return 2
    wf_cfg = cfg.settings.backtest.walkforward

    wf = run_walkforward_harness(
        strategy,
        strategy_cfg,
        df,
        cost_model=cost_model,
        train_months=wf_cfg.train_months,
        test_months=wf_cfg.test_months,
        step_months=wf_cfg.step_months,
        warmup_bars=_WARMUP_BARS,
    )

    vgr = _evaluate(cfg, wf.oos_metrics, permutation_p=wf.permutation_p)
    _print_report(args, vgr, wf.permutation_p)

    async with session_factory() as session:
        await BacktestRunRepo(session).add(
            strategy=args.strategy,
            instrument=args.symbol,
            window_start=args.from_date,
            window_end=args.to_date,
            is_oos=True,
            metrics={
                "n_trades_oos": vgr.n_trades_oos,
                "expectancy_oos": vgr.expectancy_oos,
                "profit_factor_oos": vgr.profit_factor_oos,
                "max_drawdown_pct": vgr.max_drawdown_pct,
                "dsr_probability": vgr.dsr_probability,
                "pbo": vgr.pbo,
                "permutation_p": vgr.permutation_p,
                "n_trials": 1,
            },
            gates={"all_passed": vgr.all_passed, "per_gate": vgr.gate_results},
            params={
                "tf": args.tf,
                "walkforward": wf_cfg.model_dump(),
                "baseline": args.baseline_strategy,
            },
        )
        await session.commit()

    return 0 if vgr.all_passed else 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    cfg = AppConfig.load()
    raise SystemExit(asyncio.run(_run(args, cfg)))


if __name__ == "__main__":
    main()
