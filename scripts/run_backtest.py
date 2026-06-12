"""Run backtest CLI — real strategy→engine harness (V1, V2, V3).

Usage:
    # Single in-sample backtest:
    uv run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument XAUUSD

    # Walk-forward OOS backtest:
    uv run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument XAUUSD --walkforward

    # Smart-exit A/B comparison:
    uv run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument XAUUSD --walkforward --smart-exit

Output: reports/backtest_{strategy}_{instrument}_{date}.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import structlog

from rtrade.backtest.costs import load_cost_models
from rtrade.backtest.harness import (
    HarnessResult,
    WalkForwardHarnessResult,
    run_harness,
    run_walkforward_harness,
)
from rtrade.backtest.smart_exit import SmartExitConfig
from rtrade.core.config import AppConfig
from rtrade.core.constants import Timeframe
from rtrade.indicators.engine import compute as compute_indicators
from rtrade.persistence.db import create_engine, create_session_factory
from rtrade.persistence.repositories import CandleRepo, InstrumentRepo
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig

logger = structlog.get_logger(__name__)

MIN_BARS = 5000


async def _load_candles(instrument: str, *, db_url: str) -> pd.DataFrame:
    """Load all 1H candles from DB for an instrument → DataFrame."""
    engine = create_engine(db_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            row = await InstrumentRepo(session).get_by_symbol(instrument)
            if row is None:
                logger.error("instrument not found in DB", symbol=instrument)
                sys.exit(1)
            candles = await CandleRepo(session).get_range(
                row.id,
                Timeframe.H1,
                datetime(2000, 1, 1, tzinfo=UTC),
                datetime.now(UTC) + timedelta(days=1),
            )
    finally:
        await engine.dispose()

    if len(candles) < MIN_BARS:
        logger.error(
            "insufficient data — run backfill first",
            symbol=instrument,
            candles=len(candles),
            required=MIN_BARS,
        )
        sys.exit(1)

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
    df.index = pd.DatetimeIndex(df["ts"])
    df = df.drop(columns=["ts"])
    return df


def _load_strategy_cfg(strategy_name: str) -> StrategyConfig:
    """Load strategy config from YAML."""
    import yaml

    path = Path("config/strategies") / f"{strategy_name}.yaml"
    if not path.exists():
        logger.error("strategy config not found", path=str(path))
        sys.exit(1)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return StrategyConfig(raw=raw)


def _format_harness_report(
    strategy: str,
    instrument: str,
    result: HarnessResult,
    *,
    label: str = "In-Sample",
) -> str:
    """Format a single harness result as markdown."""
    m = result.metrics
    g = result.gates
    lines = [
        f"## {label} Results",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| N signals | {len(result.signals)} |",
        f"| N trades (filled) | {m.n_trades} |",
        f"| Win rate | {m.win_rate:.2%} |",
        f"| Expectancy (avg R) | {m.expectancy:.4f} |",
        f"| Profit factor | {m.profit_factor:.2f} |",
        f"| Max drawdown | {m.max_drawdown_pct:.1f}% |",
        f"| Sharpe (ann.) | {m.sharpe_ratio:.2f} |",
        f"| Total return | {m.total_return_pct:.1f}% |",
        f"| DSR probability | {g.dsr_probability:.4f} |",
        f"| Permutation p | {result.permutation_p:.4f} |",
        "",
        "### Validation Gates",
        "",
        "| Gate | Result |",
        "|------|--------|",
    ]
    for gate_name, passed in g.gate_results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        lines.append(f"| {gate_name} | {status} |")
    lines.append(f"| **ALL PASSED** | **{'✅' if g.all_passed else '❌'}** |")
    lines.append("")

    # First 10 trades for debug.
    filled = [t for t in result.backtest.trades if t.fill_bar is not None][:10]
    if filled:
        lines.append("### Sample Trades (first 10)")
        lines.append("")
        lines.append("| # | Dir | Entry | SL | TP | Exit | Reason | R |")
        lines.append("|---|-----|-------|----|----|------|--------|---|")
        for i, t in enumerate(filled):
            lines.append(
                f"| {i + 1} | {t.direction} | {t.fill_price:.2f} "
                f"| {t.stop_loss:.2f} | {t.take_profit:.2f} "
                f"| {t.exit_price:.2f} | {t.exit_reason} "
                f"| {t.r_multiple:.2f} |"
            )
    lines.append("")
    return "\n".join(lines)


def _format_wf_report(
    strategy: str,
    instrument: str,
    result: WalkForwardHarnessResult,
    *,
    label: str = "Walk-Forward OOS",
) -> str:
    """Format a walk-forward result as markdown."""
    m = result.oos_metrics
    g = result.oos_gates
    lines = [
        f"## {label} Results",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| N windows | {len(result.per_window_metrics)} |",
        f"| N trades OOS | {m.n_trades} |",
        f"| Win rate OOS | {m.win_rate:.2%} |",
        f"| Expectancy OOS | {m.expectancy:.4f} |",
        f"| Profit factor OOS | {m.profit_factor:.2f} |",
        f"| Max drawdown OOS | {m.max_drawdown_pct:.1f}% |",
        f"| Sharpe OOS | {m.sharpe_ratio:.2f} |",
        f"| Total return OOS | {m.total_return_pct:.1f}% |",
        f"| DSR probability | {g.dsr_probability:.4f} |",
        f"| Permutation p | {result.permutation_p:.4f} |",
        "",
        "### Validation Gates",
        "",
        "| Gate | Result |",
        "|------|--------|",
    ]
    for gate_name, passed in g.gate_results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        lines.append(f"| {gate_name} | {status} |")
    lines.append(f"| **ALL PASSED** | **{'✅' if g.all_passed else '❌'}** |")
    lines.append("")

    # Per-window breakdown.
    if result.per_window_metrics:
        lines.append("### Per-Window Breakdown")
        lines.append("")
        lines.append("| Window | N signals | N trades | Expectancy | Win Rate |")
        lines.append("|--------|-----------|----------|------------|----------|")
        for pw in result.per_window_metrics:
            lines.append(
                f"| {pw.get('test_start', '?')}→{pw.get('test_end', '?')} "
                f"| {pw.get('n_signals', 0)} | {pw.get('n_trades', 0)} "
                f"| {pw.get('expectancy', 'N/A')} | {pw.get('win_rate', 'N/A')} |"
            )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest for a strategy × instrument")
    parser.add_argument(
        "--strategy",
        type=str,
        required=True,
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy name",
    )
    parser.add_argument("--instrument", type=str, required=True, help="Instrument symbol")
    parser.add_argument(
        "--walkforward", action="store_true", help="Run walk-forward OOS backtest (V2)"
    )
    parser.add_argument(
        "--smart-exit", action="store_true", help="Compare baseline vs smart-exit (V3)"
    )
    parser.add_argument(
        "--report", action="store_true", default=True, help="Generate markdown report"
    )
    parser.add_argument("--output-dir", type=str, default="reports", help="Output directory")
    args = parser.parse_args()

    cfg = AppConfig.load()
    strategy_cls = STRATEGY_REGISTRY[args.strategy]
    strategy = strategy_cls()
    strategy_cfg = _load_strategy_cfg(args.strategy)

    # Load cost model.
    cost_models = load_cost_models()
    cost_model = cost_models.get(args.instrument)
    if cost_model is None:
        logger.warning("no cost model found — using zero costs", instrument=args.instrument)

    # Load candles from DB.
    logger.info("loading candles from DB", instrument=args.instrument)
    df = asyncio.run(_load_candles(args.instrument, db_url=cfg.secrets.database_url))
    logger.info("candles loaded", n_bars=len(df))

    # Compute indicators once.
    df = compute_indicators(df)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(UTC).strftime("%Y%m%d")

    report_parts: list[str] = [
        f"# Backtest Report: {args.strategy} × {args.instrument}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Data range: {df.index[0]} → {df.index[-1]}",
        f"Total bars: {len(df)}",
        "",
    ]

    if args.walkforward:
        # ---- Walk-forward mode (V2) ----
        logger.info("running walk-forward harness")
        wf_result = run_walkforward_harness(
            strategy,
            strategy_cfg,
            df,
            cost_model=cost_model,
        )
        report_parts.append(_format_wf_report(args.strategy, args.instrument, wf_result))

        if args.smart_exit:
            # ---- Smart-exit A/B (V3) ----
            logger.info("running walk-forward with smart-exit")
            se_cfg = SmartExitConfig()  # default: partial 0.5@1R + BE + trail
            wf_se_result = run_walkforward_harness(
                strategy,
                strategy_cfg,
                df,
                cost_model=cost_model,
                smart_exit=se_cfg,
            )
            report_parts.append(
                _format_wf_report(
                    args.strategy,
                    args.instrument,
                    wf_se_result,
                    label="Walk-Forward OOS + Smart Exit (partial 50%@1R + BE + trail)",
                )
            )

            # Delta comparison.
            delta_exp = wf_se_result.oos_metrics.expectancy - wf_result.oos_metrics.expectancy
            delta_pf = wf_se_result.oos_metrics.profit_factor - wf_result.oos_metrics.profit_factor
            report_parts.append("## Exit Policy Comparison")
            report_parts.append("")
            report_parts.append("| Metric | Baseline | Smart Exit | Delta |")
            report_parts.append("|--------|----------|------------|-------|")
            report_parts.append(
                f"| Expectancy | {wf_result.oos_metrics.expectancy:.4f} "
                f"| {wf_se_result.oos_metrics.expectancy:.4f} "
                f"| {delta_exp:+.4f} |"
            )
            report_parts.append(
                f"| Profit Factor | {wf_result.oos_metrics.profit_factor:.2f} "
                f"| {wf_se_result.oos_metrics.profit_factor:.2f} "
                f"| {delta_pf:+.2f} |"
            )
            report_parts.append(
                f"| Max DD | {wf_result.oos_metrics.max_drawdown_pct:.1f}% "
                f"| {wf_se_result.oos_metrics.max_drawdown_pct:.1f}% | |"
            )
            report_parts.append("")

            champion = "smart_exit" if delta_exp > 0 else "baseline"
            report_parts.append(f"**Champion: {champion}** (based on OOS expectancy delta)")
            report_parts.append("")
    else:
        # ---- Single in-sample mode (V1) ----
        logger.info("running in-sample harness")
        result = run_harness(strategy, strategy_cfg, df, cost_model=cost_model)
        report_parts.append(_format_harness_report(args.strategy, args.instrument, result))

        if args.smart_exit:
            se_cfg = SmartExitConfig()
            se_result = run_harness(
                strategy, strategy_cfg, df, cost_model=cost_model, smart_exit=se_cfg
            )
            report_parts.append(
                _format_harness_report(
                    args.strategy,
                    args.instrument,
                    se_result,
                    label="In-Sample + Smart Exit",
                )
            )

    # Write report.
    mode_label = "wf" if args.walkforward else "is"
    report_name = f"backtest_{args.strategy}_{args.instrument}_{mode_label}_{date_str}.md"
    report_path = output_dir / report_name
    report_content = "\n".join(report_parts)
    report_path.write_text(report_content, encoding="utf-8")
    logger.info("report saved", path=str(report_path))
    print(f"\n✅ Report: {report_path}")  # noqa: T201
    print(report_content)  # noqa: T201


if __name__ == "__main__":
    main()
