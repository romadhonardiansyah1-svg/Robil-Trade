"""Layered LLM budget guard (FR-LLM-09/10/11, G-11).

4 caps independen: tokens/scan, USD/day (hard abort), wall-clock/scan,
steps/scan. Pricing dari litellm cost metadata (NFR-COST-02). Pada breach
apapun → set budget_stop + return reason. Cascade caller fallback/abstain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import time
from typing import Literal

import structlog

from rtrade.core.config import LLMBudgetSettings

logger = structlog.get_logger(__name__)

BudgetStopReason = Literal["tokens", "usd_day", "wall", "steps"]


@dataclass
class BudgetState:
    """Per-scan mutable budget state."""

    scan_tokens: int = 0
    scan_steps: int = 0
    scan_started_monotonic: float = field(default_factory=time.monotonic)
    day_usd: float = 0.0
    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    budget_stop: BudgetStopReason | None = None


class BudgetGuard:
    """Enforce 4-cap budget: tokens/scan, USD/day, wall-clock/scan, steps/scan."""

    def __init__(self, caps: LLMBudgetSettings) -> None:
        self._caps = caps

    def start_scan(self) -> BudgetState:
        return BudgetState()

    def reset_day_if_needed(self, state: BudgetState) -> None:
        today = datetime.now(UTC).date()
        if state.day != today:
            state.day = today
            state.day_usd = 0.0
            state.budget_stop = None  # reset daily abort

    def record(
        self,
        state: BudgetState,
        *,
        tokens: int = 0,
        usd: float = 0.0,
        steps: int = 1,
    ) -> BudgetStopReason | None:
        if state.budget_stop is not None:
            return state.budget_stop  # sudah stop, short-circuit
        self.reset_day_if_needed(state)
        state.scan_tokens += tokens
        state.day_usd += usd
        state.scan_steps += steps
        elapsed = time.monotonic() - state.scan_started_monotonic

        if state.scan_tokens > self._caps.max_tokens_per_scan:
            state.budget_stop = "tokens"
        elif state.day_usd >= self._caps.max_usd_per_day:
            state.budget_stop = "usd_day"
        elif elapsed > self._caps.max_wall_seconds_per_scan:
            state.budget_stop = "wall"
        elif state.scan_steps > self._caps.max_steps_per_scan:
            state.budget_stop = "steps"

        if state.budget_stop:
            logger.warning(
                "budget_stop triggered",
                reason=state.budget_stop,
                scan_tokens=state.scan_tokens,
                day_usd=state.day_usd,
                elapsed=elapsed,
                scan_steps=state.scan_steps,
            )
        return state.budget_stop

    def at_80pct_daily(self, state: BudgetState) -> bool:
        return state.day_usd >= 0.8 * self._caps.max_usd_per_day
