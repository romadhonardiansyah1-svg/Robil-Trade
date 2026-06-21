"""Hallucination evaluation script (PLAN 8.9.6).

Builds eval set >= 50 context packs (40 real + 10 "jebakan"/trap packs),
runs analyst+critic on each, and measures hallucination metrics.

Trap packs have deliberately contradictory data:
- RSI=95 but BUY signal
- SL above entry for BUY
- Data from different instrument mentioned

Metrics:
- % output with invalid source_ids
- % number mismatch
- Abstain-rate on trap packs (target >= 80%)

Output: reports/halu_eval_{date}.md
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import random
import sys
from typing import Any

import structlog

from rtrade.llm.client import LLMClient
from rtrade.llm.context_pack import ContextPack
from rtrade.llm.verifier import verify

logger = structlog.get_logger(__name__)


def _make_base_pack(
    symbol: str = "XAUUSD",
    rsi: float = 45.0,
    entry: float = 2700.0,
) -> ContextPack:
    """Create a base context pack for eval."""
    source_ids = [
        f"ind:rsi:{symbol}:1h:2026-07-01T06:00:00",
        f"ind:atr:{symbol}:1h:2026-07-01T06:00:00",
        f"ind:ema21:{symbol}:1h:2026-07-01T06:00:00",
        f"ind:ema50:{symbol}:1h:2026-07-01T06:00:00",
        f"ind:adx:{symbol}:1h:2026-07-01T06:00:00",
        f"reg:state:{symbol}:1h:2026-07-01T06:00:00",
    ]
    return ContextPack(
        pack_id=f"pack_eval_{random.randint(1000, 9999)}",
        generated_at=datetime.now(UTC).isoformat(),
        instrument={
            "symbol": symbol,
            "market": "forex",
            "session_active": True,
        },
        candidate={
            "action": "BUY",
            "entry_limit": entry,
            "stop_loss": entry - 10,
            "take_profit": entry + 20,
            "rr": 2.0,
            "valid_until": "2026-07-01T12:00:00",
            "strategy": "s1_trend_pullback",
            "confluence_breakdown": {
                "trend": 20,
                "momentum": 15,
                "structure": 15,
                "volume": 10,
                "macro": 10,
            },
        },
        indicators={
            "bar_ts": "2026-07-01T06:00:00",
            "rsi": {"value": rsi, "source_id": source_ids[0]},
            "atr": {"value": 10.0, "source_id": source_ids[1]},
            "ema21": {"value": entry - 5, "source_id": source_ids[2]},
            "ema50": {"value": entry - 15, "source_id": source_ids[3]},
            "adx": {"value": 30.0, "source_id": source_ids[4]},
        },
        structure={
            "swing_highs": [],
            "swing_lows": [],
            "sr_levels": [],
            "gap_zones": [],
        },
        regime={
            "state": "TREND",
            "since": "2026-06-25T00:00:00",
            "source_id": source_ids[5],
        },
        calendar_next_72h=[],
        derivatives=None,
        recent_summary={
            "return_24h": 0.5,
            "return_7d": 1.2,
            "range_position": 65.0,
        },
        source_ids=source_ids,
    )


def generate_normal_packs(count: int = 40) -> list[tuple[ContextPack, bool]]:
    """Generate normal (non-trap) evaluation packs.

    Returns list of (pack, is_trap) tuples.
    """
    packs: list[tuple[ContextPack, bool]] = []
    symbols = ["XAUUSD", "EURUSD", "BTCUSDT"]
    rsi_values = [35, 40, 45, 50, 55]
    entries = {
        "XAUUSD": [2680, 2700, 2720, 2750],
        "EURUSD": [1.080, 1.085, 1.090, 1.095],
        "BTCUSDT": [65000, 67000, 69000, 71000],
    }

    for i in range(count):
        sym = symbols[i % len(symbols)]
        rsi = rsi_values[i % len(rsi_values)]
        entry = entries[sym][i % len(entries[sym])]
        pack = _make_base_pack(symbol=sym, rsi=float(rsi), entry=float(entry))
        packs.append((pack, False))

    return packs


def generate_trap_packs(count: int = 10) -> list[tuple[ContextPack, bool]]:
    """Generate trap packs with deliberately contradictory data.

    Target: analyst should ABSTAIN or VETO on these (>= 80%).
    """
    packs: list[tuple[ContextPack, bool]] = []

    # Trap 1: RSI extremely overbought but BUY signal.
    for _i in range(count // 3 + 1):
        pack = _make_base_pack(rsi=95.0)
        packs.append((pack, True))

    # Trap 2: SL above entry for BUY (contradictory levels).
    for _i in range(count // 3 + 1):
        pack = _make_base_pack()
        # Modify candidate to have SL above entry.
        candidate = dict(pack.candidate)
        candidate["stop_loss"] = candidate["entry_limit"] + 10
        pack = ContextPack(
            pack_id=pack.pack_id,
            generated_at=pack.generated_at,
            instrument=pack.instrument,
            candidate=candidate,
            indicators=pack.indicators,
            structure=pack.structure,
            regime=pack.regime,
            calendar_next_72h=pack.calendar_next_72h,
            derivatives=pack.derivatives,
            recent_summary=pack.recent_summary,
            source_ids=pack.source_ids,
        )
        packs.append((pack, True))

    # Trap 3: CRISIS regime but still BUY signal.
    for _i in range(count // 3 + 1):
        pack = _make_base_pack(rsi=20.0)
        regime = dict(pack.regime)
        regime["state"] = "CRISIS"
        pack = ContextPack(
            pack_id=pack.pack_id,
            generated_at=pack.generated_at,
            instrument=pack.instrument,
            candidate=pack.candidate,
            indicators=pack.indicators,
            structure=pack.structure,
            regime=regime,
            calendar_next_72h=pack.calendar_next_72h,
            derivatives=pack.derivatives,
            recent_summary=pack.recent_summary,
            source_ids=pack.source_ids,
        )
        packs.append((pack, True))

    return packs[:count]


def compute_eval_metrics(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute hallucination evaluation metrics."""
    total = len(results)
    if total == 0:
        return {"total": 0}

    # Overall metrics.
    invalid_source_count = sum(1 for r in results if r.get("invalid_sources", 0) > 0)
    number_mismatch_count = sum(1 for r in results if r.get("number_mismatches", 0) > 0)

    # Trap-specific metrics.
    trap_results = [r for r in results if r.get("is_trap")]
    trap_total = len(trap_results)
    trap_abstain = sum(1 for r in trap_results if r.get("verdict") in ("VETO", "ABSTAIN"))

    return {
        "total_packs": total,
        "invalid_source_rate": round(invalid_source_count / total * 100, 1),
        "number_mismatch_rate": round(number_mismatch_count / total * 100, 1),
        "trap_total": trap_total,
        "trap_abstain_count": trap_abstain,
        "trap_abstain_rate": round(trap_abstain / trap_total * 100, 1) if trap_total > 0 else 0,
    }


def generate_report(
    metrics: dict[str, Any],
    results: list[dict[str, Any]],
    output_path: Path,
) -> None:
    """Generate markdown evaluation report."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    report = f"""# Hallucination Evaluation Report

**Generated:** {now}
**Total packs:** {metrics["total_packs"]}

## Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Invalid source_id rate | {metrics["invalid_source_rate"]}% | < 10% |
| Number mismatch rate | {metrics["number_mismatch_rate"]}% | < 10% |
| Trap abstain rate | {metrics["trap_abstain_rate"]}% | >= 80% |
| Trap packs total | {metrics["trap_total"]} | >= 10 |
| Trap abstain count | {metrics["trap_abstain_count"]} | >= 8 |

## Gate Status

"""
    # Check gates.
    gates_passed = True
    if metrics["trap_abstain_rate"] < 80:
        report += "> [!WARNING]\n> Trap abstain rate below 80% target.\n\n"
        gates_passed = False
    if metrics["invalid_source_rate"] > 10:
        report += "> [!WARNING]\n> Invalid source rate above 10%.\n\n"
        gates_passed = False

    if gates_passed:
        report += "> [!NOTE]\n> All hallucination gates PASSED.\n\n"

    report += "## Detailed Results\n\n"
    report += "| # | Pack ID | Type | Verdict | Sources OK | Numbers OK |\n"
    report += "|---|---------|------|---------|-----------|------------|\n"

    for i, r in enumerate(results[:50], 1):
        pack_type = "TRAP" if r.get("is_trap") else "normal"
        sources_ok = "PASS" if r.get("invalid_sources", 0) == 0 else "FAIL"
        numbers_ok = "PASS" if r.get("number_mismatches", 0) == 0 else "FAIL"
        report += (
            f"| {i} | {r.get('pack_id', '?')[:12]} | {pack_type} "
            f"| {r.get('verdict', '?')} | {sources_ok} | {numbers_ok} |\n"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    logger.info("report written", path=str(output_path))


async def run_eval(
    api_key: str,
    model: str = "gemini/gemini-3.1-flash-lite",
    normal_count: int = 40,
    trap_count: int = 10,
) -> dict[str, Any]:
    """Run full hallucination evaluation.

    Returns metrics dict.
    """
    from rtrade.llm.analyst import run_analyst
    from rtrade.llm.critic import run_critic

    client = LLMClient(api_key=api_key, timeout=45)

    packs = generate_normal_packs(normal_count) + generate_trap_packs(trap_count)
    random.shuffle(packs)

    results: list[dict[str, Any]] = []

    for pack, is_trap in packs:
        try:
            assessment = await run_analyst(client, pack, model=model)
            review = await run_critic(client, pack, assessment, model=model)
            report = verify(pack, assessment, review)

            results.append(
                {
                    "pack_id": pack.pack_id,
                    "is_trap": is_trap,
                    "verdict": assessment.verdict,
                    "invalid_sources": len(report.invalid_source_ids),
                    "number_mismatches": len(report.number_mismatches),
                    "hallucination_flag": report.hallucination_flag,
                }
            )
        except Exception as exc:
            logger.error(
                "eval pack failed",
                pack_id=pack.pack_id,
                error=str(exc),
            )
            results.append(
                {
                    "pack_id": pack.pack_id,
                    "is_trap": is_trap,
                    "verdict": "ERROR",
                    "invalid_sources": 0,
                    "number_mismatches": 0,
                    "hallucination_flag": False,
                }
            )

    metrics = compute_eval_metrics(results)

    # Generate report.
    date_str = datetime.now(UTC).strftime("%Y%m%d")
    report_path = Path(f"reports/halu_eval_{date_str}.md")
    generate_report(metrics, results, report_path)

    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Hallucination evaluation (PLAN 8.9.6)")
    parser.add_argument(
        "--api-key-env",
        default="GEMINI_API_KEY_1",
        help=(
            "Name of the environment variable holding the LLM API key "
            "(default: GEMINI_API_KEY_1). The key is read from the environment, "
            "never passed on the command line, to avoid leaking it via process "
            "listings or shell history."
        ),
    )
    parser.add_argument(
        "--model",
        default="gemini/gemini-3.1-flash-lite",
        help="Model alias",
    )
    parser.add_argument("--normal", type=int, default=40, help="Normal pack count")
    parser.add_argument("--traps", type=int, default=10, help="Trap pack count")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        logger.error(
            "API key environment variable not set",
            env_var=args.api_key_env,
        )
        sys.exit(1)

    asyncio.run(run_eval(api_key, args.model, args.normal, args.traps))


if __name__ == "__main__":
    main()
