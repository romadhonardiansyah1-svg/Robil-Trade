"""Guardrail gate -- 13 guardrails (full set P1+P2) (PLAN 8.8).

Called AFTER the pipeline (or directly after risk module in P1 without LLM).
Return GateResult(passed, failures). ONE failure = signal REJECTED with full
audit trail.

P1 active guardrails:
    GR-01: Schema valid (SignalCandidate Pydantic parse)
    GR-02: Direction consistency (BUY: SL<entry<TP)
    GR-03: R:R floor ≥ 1.5
    GR-04: SL distance [0.5×ATR, 3.0×ATR]
    GR-05: Risk cap ≤ 2.0%
    GR-06: Freshness + price drift
    GR-07: News blackout
    GR-08: Regime gate (not CRISIS, strategy matches regime)
    GR-12: Rate cap (≤ 3 signals/day/instrument)
    GR-13: Expectancy guard

P2 additions (now active):
    GR-09: Confidence floor >= 0.55
    GR-10: No-LLM-number-mutation (entry/SL/TP/size bit-perfect)
    GR-11: Citations (sources[] non-empty, all source_ids in pack)
"""

from __future__ import annotations

from datetime import datetime

import structlog

from rtrade.core.constants import Action, Regime, Timeframe
from rtrade.core.timeutil import ensure_utc, is_candle_fresh
from rtrade.risk.limits import check_daily_limit, check_expectancy_guard
from rtrade.risk.news_filter import check_news_blackout
from rtrade.signals.schemas import GateFailure, GateResult, SignalCandidate

logger = structlog.get_logger(__name__)


def run_gate(
    candidate: SignalCandidate,
    *,
    # GR-06: freshness + drift
    latest_candle_ts: datetime | None = None,
    timeframe: Timeframe = Timeframe.H1,
    staleness_factor: float = 2.0,
    live_price: float | None = None,
    live_quote_required: bool = False,  # G-09: fail-CLOSE if True and quote unavailable
    price_drift_max_pct: float = 0.5,
    now: datetime | None = None,
    # GR-07: news
    events: list[dict[str, object]] | None = None,
    related_currencies: list[str] | None = None,
    news_blackout_before_min: int = 30,
    news_blackout_after_min: int = 15,
    # GR-07b: calendar staleness
    calendar_stale: bool = False,
    # GR-08: regime
    regime: Regime | None = None,
    required_regime: Regime | None = None,
    # GR-12: rate
    signals_today: int = 0,
    max_signals_per_day: int = 3,
    # GR-13: expectancy
    paper_outcomes: list[float] | None = None,
    expectancy_window: int = 30,
    # --- P2 additions ---
    # GR-09: confidence floor
    confidence: float | None = None,
    confidence_min: float = 0.55,
    # GR-10: no-LLM-number-mutation
    original_candidate: SignalCandidate | None = None,
    # GR-11: citations
    sources: list[str] | None = None,
    pack_source_ids: set[str] | None = None,
    # --- B6: caller-declared mandatory gates (fail CLOSED on missing input) ---
    require: set[str] | None = None,
) -> GateResult:
    """Run all P1 guardrails on a SignalCandidate.

    Returns GateResult with passed=True only if ALL guardrails pass.

    ``require`` (B6 / fail-closed): a set of gate IDs the CALLER declares MUST be
    evaluated. Several gates only run when their input is provided (they are
    wrapped in ``if x is not None``), so a caller that OMITS an input would
    silently SKIP a safety gate — a fail-OPEN hole. For every gate id listed in
    ``require`` whose backing input is missing/None, a ``GateFailure`` with
    reason ``"required input missing — fail closed"`` is appended, turning
    omission into a REJECTION with a full audit trail. When ``require is None``
    (the default) behaviour is unchanged, preserving the no-LLM and crypto paths
    plus existing callers/tests.

    Requirable gate -> backing input mapping:
        GR-06 -> ``latest_candle_ts`` (freshness; ``live_price`` is enforced
                 separately by ``live_quote_required``)
        GR-07 -> ``events`` AND ``related_currencies`` (news blackout)
        GR-08 -> ``regime`` (regime gate)
        GR-09 -> ``confidence`` (confidence floor)
        GR-11 -> ``sources`` (citations)
        GR-13 -> ``paper_outcomes`` (expectancy guard)
    """
    from rtrade.core.timeutil import utcnow

    failures: list[GateFailure] = []
    now_ts = ensure_utc(now) if now is not None else utcnow()

    # --- B6: fail CLOSED when a CALLER-required gate's input is absent ---
    if require:
        missing_reason = "required input missing — fail closed"
        required_input_present: dict[str, bool] = {
            "GR-06": latest_candle_ts is not None,
            "GR-07": events is not None and related_currencies is not None,
            "GR-08": regime is not None,
            "GR-09": confidence is not None,
            "GR-11": sources is not None,
            "GR-13": paper_outcomes is not None,
        }
        for gate_id in sorted(require):
            if not required_input_present.get(gate_id, True):
                failures.append(GateFailure(gate_id=gate_id, reason=missing_reason))

    # --- GR-01: Schema validity ---
    # Already guaranteed by Pydantic validation when creating SignalCandidate.
    # If we got here, GR-01 passes.

    # --- GR-02: Direction consistency ---
    e = candidate.levels.entry_limit
    sl = candidate.levels.stop_loss
    tp = candidate.levels.take_profit

    if candidate.action == Action.BUY and not (sl < e < tp):
        failures.append(GateFailure(gate_id="GR-02", reason="BUY: SL < entry < TP violated"))
    elif candidate.action == Action.SELL and not (tp < e < sl):
        failures.append(GateFailure(gate_id="GR-02", reason="SELL: TP < entry < SL violated"))

    # --- GR-03: R:R floor ---
    sl_dist = abs(e - sl)
    tp_dist = abs(tp - e)
    if sl_dist > 0:
        rr = tp_dist / sl_dist
        if rr < 1.5:
            failures.append(GateFailure(gate_id="GR-03", reason=f"R:R {rr:.2f} < 1.5"))
    else:
        failures.append(GateFailure(gate_id="GR-03", reason="SL distance is zero"))

    # --- GR-04: SL distance in ATR multiples ---
    atr = candidate.levels.atr_at_signal
    if atr > 0 and sl_dist > 0:
        atr_mult = sl_dist / atr
        if not (0.5 <= atr_mult <= 3.0):
            failures.append(
                GateFailure(
                    gate_id="GR-04",
                    reason=f"SL distance {atr_mult:.2f}x ATR outside [0.5, 3.0]",
                )
            )

    # --- GR-05: Risk cap ---
    if candidate.risk_pct > 2.0:
        failures.append(
            GateFailure(
                gate_id="GR-05",
                reason=f"risk_pct {candidate.risk_pct}% > 2.0% cap",
            )
        )

    # --- GR-06: Freshness + price drift ---
    if latest_candle_ts is not None and not is_candle_fresh(
        latest_candle_ts, timeframe, staleness_factor=staleness_factor, now=now_ts
    ):
        failures.append(
            GateFailure(
                gate_id="GR-06",
                reason="candle data is stale (exceeds staleness factor)",
            )
        )

    # --- GR-06: Freshness + price drift (fail-CLOSE on missing required quote, G-09) ---
    if live_quote_required and live_price is None:
        failures.append(
            GateFailure(
                gate_id="GR-06",
                reason="required live quote unavailable — fail-closed (abstain)",
            )
        )
    elif live_price is not None and price_drift_max_pct > 0:
        drift_pct = abs(live_price - e) / e * 100
        if drift_pct > price_drift_max_pct:
            failures.append(
                GateFailure(
                    gate_id="GR-06",
                    reason=f"price drift {drift_pct:.2f}% > {price_drift_max_pct}% max",
                )
            )

    # --- GR-07b: fail-CLOSED when the economic calendar is stale ---
    if calendar_stale:
        failures.append(
            GateFailure(
                gate_id="GR-07",
                reason="economic calendar is stale/empty — fail-closed for non-crypto",
            )
        )

    # --- GR-07: News blackout ---
    if events is not None and related_currencies is not None:
        blocked, reason = check_news_blackout(
            events,
            related_currencies,
            now_ts,
            before_min=news_blackout_before_min,
            after_min=news_blackout_after_min,
        )
        if blocked and reason:
            failures.append(GateFailure(gate_id="GR-07", reason=reason))

    # --- GR-08: Regime gate ---
    if regime is not None:
        if regime == Regime.CRISIS:
            failures.append(
                GateFailure(
                    gate_id="GR-08",
                    reason="regime is CRISIS — all new signals blocked",
                )
            )
        elif required_regime is not None and regime != required_regime:
            failures.append(
                GateFailure(
                    gate_id="GR-08",
                    reason=f"regime {regime.value} doesn't match strategy requirement {required_regime.value}",
                )
            )

    # --- GR-12: Daily rate limit ---
    allowed, reason = check_daily_limit(signals_today, max_signals_per_day)
    if not allowed and reason:
        failures.append(GateFailure(gate_id="GR-12", reason=reason))

    # --- GR-13: Expectancy guard ---
    if paper_outcomes is not None:
        ok, reason = check_expectancy_guard(paper_outcomes, expectancy_window)
        if not ok and reason:
            failures.append(GateFailure(gate_id="GR-13", reason=reason))

    # --- GR-09: Confidence floor (P2) ---
    if confidence is not None and confidence < confidence_min:
        failures.append(
            GateFailure(
                gate_id="GR-09",
                reason=(f"confidence {confidence:.2f} < {confidence_min:.2f} minimum"),
            )
        )

    # --- GR-10: No-LLM-number-mutation (P2, MOST IMPORTANT) ---
    if original_candidate is not None:
        oc = original_candidate
        mutations: list[str] = []
        if candidate.levels.entry_limit != oc.levels.entry_limit:
            mutations.append(f"entry {oc.levels.entry_limit} -> {candidate.levels.entry_limit}")
        if candidate.levels.stop_loss != oc.levels.stop_loss:
            mutations.append(f"SL {oc.levels.stop_loss} -> {candidate.levels.stop_loss}")
        if candidate.levels.take_profit != oc.levels.take_profit:
            mutations.append(f"TP {oc.levels.take_profit} -> {candidate.levels.take_profit}")
        if candidate.position_size != oc.position_size:
            mutations.append(f"size {oc.position_size} -> {candidate.position_size}")
        if mutations:
            failures.append(
                GateFailure(
                    gate_id="GR-10",
                    reason=("LLM mutated numbers (CRITICAL): " + "; ".join(mutations)),
                )
            )

    # --- GR-11: Citations (P2) ---
    if sources is not None:
        if len(sources) == 0:
            failures.append(
                GateFailure(
                    gate_id="GR-11",
                    reason="sources[] is empty",
                )
            )
        elif pack_source_ids is not None:
            invalid = [s for s in sources if s not in pack_source_ids]
            if invalid:
                failures.append(
                    GateFailure(
                        gate_id="GR-11",
                        reason=(f"{len(invalid)} invalid source_ids: {invalid[:3]}"),
                    )
                )

    passed = len(failures) == 0

    if not passed:
        logger.warning(
            "guardrail gate FAILED",
            candidate_id=candidate.candidate_id,
            failures=[f.gate_id for f in failures],
        )
    else:
        logger.info(
            "guardrail gate PASSED",
            candidate_id=candidate.candidate_id,
        )

    return GateResult(passed=passed, failures=failures)
