"""Composite economic-calendar provider (FR-CAL-02).

Tries sources in configured order. Records per-source health. Emits alert
on each fallback transition + total staleness. Fail-CLOSE ditangani di gate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog

from rtrade.core.errors import ProviderError
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)


@dataclass
class CalendarSourceHealth:
    """Per-source health telemetry."""

    name: str
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_attempt: datetime | None = None


AlertCallback = Callable[[str], Awaitable[None]]


class CompositeCalendarProvider(CalendarProvider):
    """Try sources in order; record per-source health; alert on transitions."""

    def __init__(
        self,
        sources: list[CalendarProvider],
        *,
        names: list[str],
        alert_callback: AlertCallback | None = None,
    ) -> None:
        if len(sources) != len(names):
            raise ValueError("sources and names length must match")
        self._sources = list(zip(names, sources, strict=True))
        self._health: dict[str, CalendarSourceHealth] = {
            n: CalendarSourceHealth(name=n) for n in names
        }
        self._alert = alert_callback

    async def _emit_alert(self, message: str) -> None:
        if self._alert is not None:
            try:
                await self._alert(message)
            except Exception as exc:
                logger.warning("calendar alert callback failed", error=str(exc))

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        failed_sources: list[str] = []
        for name, provider in self._sources:
            health = self._health[name]
            health.last_attempt = datetime.now(UTC)
            # Alert BEFORE attempting next source (if a prior source failed).
            if failed_sources:
                await self._emit_alert(
                    f"⚠️ Calendar fallback: {failed_sources[-1]} gagal → coba {name}"
                )
            try:
                events = await provider.fetch_events(start, end)
            except Exception as exc:
                health.last_error = str(exc)
                health.consecutive_failures += 1
                logger.warning(
                    "calendar source failed",
                    source=name,
                    error=str(exc),
                    consecutive=health.consecutive_failures,
                )
                failed_sources.append(name)
                continue
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.last_error = None
            if failed_sources:
                await self._emit_alert(
                    f"✅ Calendar fallback recovered: {' → '.join(failed_sources)} gagal → {name} OK"
                )
            return events
        await self._emit_alert("🚨 CALENDAR: SEMUA sumber gagal (total staleness)")
        raise ProviderError("all calendar sources unavailable")

    def health_snapshot(self) -> dict[str, CalendarSourceHealth]:
        return dict(self._health)

    def freshest_last_success(self) -> datetime | None:
        times = [h.last_success for h in self._health.values() if h.last_success]
        return max(times) if times else None

    def active_tier(self) -> str | None:
        best: tuple[datetime, str] | None = None
        for h in self._health.values():
            if h.last_success is not None and (best is None or h.last_success > best[0]):
                best = (h.last_success, h.name)
        return best[1] if best else None

    async def close(self) -> None:
        for _, provider in self._sources:
            try:
                await provider.close()
            except Exception as exc:
                logger.warning("calendar provider close failed", error=str(exc))
