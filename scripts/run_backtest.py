"""Run backtest CLI (PLAN §8.11, P1-T10).

Usage:
    uv run python scripts/run_backtest.py --strategy s1 --instrument XAUUSD
    uv run python scripts/run_backtest.py --strategy s1 --instrument XAUUSD --report

Output: reports/backtest_{strategy}_{instrument}_{date}.md + equity curve PNG.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import structlog

logger = structlog.get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest for a strategy × instrument")
    parser.add_argument("--strategy", type=str, required=True, help="Strategy name (e.g. s1)")
    parser.add_argument("--instrument", type=str, required=True, help="Instrument symbol")
    parser.add_argument("--report", action="store_true", help="Generate markdown report")
    parser.add_argument("--output-dir", type=str, default="reports", help="Output directory")
    args = parser.parse_args()

    logger.info(
        "backtest starting",
        strategy=args.strategy,
        instrument=args.instrument,
    )

    # TODO: Full integration in P1-T12:
    # 1. Load config
    # 2. Load historical candle data from DB
    # 3. Compute indicators
    # 4. Run strategy signal generation
    # 5. Run backtester engine
    # 6. Compute metrics
    # 7. Run validation gates (DSR, PBO)
    # 8. Generate report

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(UTC).strftime("%Y%m%d")
    report_name = f"backtest_{args.strategy}_{args.instrument}_{date_str}.md"
    report_path = output_dir / report_name

    if args.report:
        report_content = _generate_report(
            strategy=args.strategy,
            instrument=args.instrument,
        )
        report_path.write_text(report_content, encoding="utf-8")
        logger.info("report saved", path=str(report_path))

    logger.info("backtest completed")


def _generate_report(strategy: str, instrument: str) -> str:
    """Generate a markdown backtest report."""
    return f"""# Backtest Report: {strategy.upper()} × {instrument}

Generated: {datetime.now(UTC).isoformat()}

## Configuration
- Strategy: {strategy}
- Instrument: {instrument}
- Initial equity: $10,000
- Risk per trade: 1.0%
- Costs: loaded from config/costs.yaml

## Results
*(Run backtest with data to populate)*

## Validation Gates
| Gate | Result |
|------|--------|
| OOS trades ≥ 100 | - |
| OOS expectancy > 0 | - |
| Profit factor ≥ 1.15 | - |
| Max DD ≤ 25% | - |
| DSR probability ≥ 0.90 | - |
| PBO ≤ 0.30 | - |

## Notes
- Cost model applied (config/costs.yaml)
- Walk-forward: 12mo train / 3mo test / 3mo step
- SL/TP same bar → SL first (worst-case assumption)
"""


if __name__ == "__main__":
    main()
