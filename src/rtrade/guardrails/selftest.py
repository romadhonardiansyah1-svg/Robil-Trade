"""Startup guardrail integrity self-test (S11).

Build known-bad candidates → run_gate → all MUST be rejected.
Now covers all 13 guardrails (P1-4 extended).
Returns list of failures (empty = healthy).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rtrade.core.constants import Action, Regime, Timeframe
from rtrade.guardrails.gate import run_gate
from rtrade.signals.schemas import ConfluenceBreakdown, LevelSet, SignalCandidate


def _make_candidate(**overrides: object) -> SignalCandidate:
    defaults = {
        "candidate_id": "selftest",
        "symbol": "XAUUSD",
        "timeframe": Timeframe.H1,
        "strategy": "ema_cross",
        "action": Action.BUY,
        "levels": LevelSet(
            entry_limit=2000.0,
            stop_loss=1990.0,
            take_profit=2020.0,
            atr_at_signal=5.0,
        ),
        "confluence_score": 70,
        "confluence_breakdown": ConfluenceBreakdown(
            trend=20, momentum=15, structure=15, volume=10, macro=10
        ),
        "risk_pct": 1.0,
        "position_size": 0.5,
        "valid_until": datetime.now(UTC),
        "bar_ts": datetime.now(UTC),
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    return SignalCandidate(**defaults)  # type: ignore[arg-type]


def run_guardrail_selftest() -> list[str]:
    """Run self-test: build illegal candidates for ALL 13 gates, assert all rejected.

    Returns list of failure messages (empty = healthy).
    """
    problems: list[str] = []
    good = _make_candidate()

    # --- GR-02: Direction consistency ---
    bad_dir = _make_candidate(
        action=Action.BUY,
        levels=LevelSet(
            entry_limit=2000.0,
            stop_loss=2010.0,  # SL > entry for BUY = wrong
            take_profit=2020.0,
            atr_at_signal=5.0,
        ),
    )
    result = run_gate(bad_dir)
    if result.passed:
        problems.append("GR-02: BUY with SL>entry was NOT rejected")

    # --- GR-03: R:R floor ---
    bad_rr = _make_candidate(
        levels=LevelSet(
            entry_limit=2000.0,
            stop_loss=1990.0,
            take_profit=2005.0,  # RR = 0.5 < 1.5
            atr_at_signal=5.0,
        ),
    )
    result = run_gate(bad_rr)
    if result.passed:
        problems.append("GR-03: R:R 0.5 was NOT rejected")

    # --- GR-04: SL distance ATR range ---
    bad_sl_atr = _make_candidate(
        levels=LevelSet(
            entry_limit=2000.0,
            stop_loss=1980.0,  # 20/5 = 4.0x ATR > 3.0
            take_profit=2060.0,
            atr_at_signal=5.0,
        ),
    )
    result = run_gate(bad_sl_atr)
    if result.passed:
        problems.append("GR-04: SL 4.0x ATR was NOT rejected")

    # --- GR-05: Risk cap ---
    bad_risk = _make_candidate(risk_pct=3.0)
    result = run_gate(bad_risk)
    if result.passed:
        problems.append("GR-05: risk 3.0% was NOT rejected")

    # --- GR-06: Stale candle ---
    result = run_gate(
        good,
        latest_candle_ts=datetime.now(UTC) - timedelta(hours=6),
        timeframe=Timeframe.H1,
        staleness_factor=2.0,
    )
    if result.passed:
        problems.append("GR-06: 6h old candle with 2x staleness was NOT rejected")

    # --- GR-06: Price drift ---
    result = run_gate(
        good,
        live_price=2100.0,  # 5% drift
        price_drift_max_pct=0.5,
    )
    if result.passed:
        problems.append("GR-06: price drift 5% was NOT rejected")

    # --- GR-06: fail-CLOSE on missing required quote ---
    result = run_gate(good, live_price=None, live_quote_required=True)
    if result.passed:
        problems.append("GR-06: missing required live quote was NOT rejected")

    # --- GR-07b: Calendar stale ---
    result = run_gate(good, calendar_stale=True)
    if result.passed:
        problems.append("GR-07b: stale calendar was NOT rejected")

    # --- GR-07: News blackout ---
    result = run_gate(
        good,
        events=[
            {
                "event": "CPI",
                "currency": "USD",
                "impact": "high",
                "event_time": datetime.now(UTC) + timedelta(minutes=10),
            }
        ],
        related_currencies=["USD"],
        news_blackout_before_min=30,
        news_blackout_after_min=15,
        now=datetime.now(UTC),
    )
    if result.passed:
        problems.append("GR-07: high-impact 10min away was NOT rejected")

    # --- GR-08: Regime CRISIS ---
    result = run_gate(good, regime=Regime.CRISIS)
    if result.passed:
        problems.append("GR-08: CRISIS regime was NOT rejected")

    # --- GR-09: Confidence floor ---
    result = run_gate(good, confidence=0.30, confidence_min=0.55)
    if result.passed:
        problems.append("GR-09: low confidence 0.30 was NOT rejected")

    # --- GR-10: LLM mutated entry ---
    original = _make_candidate()
    mutated = _make_candidate(
        levels=LevelSet(
            entry_limit=2000.1,
            stop_loss=1990.0,
            take_profit=2020.0,
            atr_at_signal=5.0,
        )
    )
    result = run_gate(mutated, original_candidate=original)
    if result.passed:
        problems.append("GR-10: entry mutation 2000→2000.1 was NOT rejected")

    # --- GR-11: Empty sources ---
    result = run_gate(good, sources=[])
    if result.passed:
        problems.append("GR-11: empty sources was NOT rejected")

    # --- GR-12: Daily rate limit ---
    result = run_gate(good, signals_today=5, max_signals_per_day=3)
    if result.passed:
        problems.append("GR-12: 5 signals with max 3 was NOT rejected")

    # --- GR-13: Negative expectancy ---
    result = run_gate(
        good,
        paper_outcomes=[-1.0] * 30,  # all losses
        expectancy_window=30,
    )
    if result.passed:
        problems.append("GR-13: negative expectancy was NOT rejected")

    # --- Regression check: GOOD candidate passes ---
    result = run_gate(good)
    if not result.passed:
        problems.append(
            f"REGRESSION: valid candidate rejected: {[f.reason for f in result.failures]}"
        )

    return problems
