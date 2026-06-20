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
    """Per-source health telemetry.

    ``last_success`` records the last VERIFIED (non-empty) fetch. A source that
    returns an empty list WITHOUT raising is recorded via ``last_attempt`` /
    ``last_empty`` only — it does NOT advance ``last_success``. This keeps the
    downstream staleness gate fail-CLOSED: a silently-broken source returning
    ``[]`` cannot masquerade as "fresh with zero events" (defect B1).
    """

    name: str
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_attempt: datetime | None = None
    last_empty: datetime | None = None


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
        empty_sources: list[str] = []
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
            # FAILOVER ON EMPTY (B1): an empty list WITHOUT error is NOT a verified
            # success. Do not advance last_success and do not stop the loop — a
            # working source's events must be preferred over a broken source's [].
            if not events:
                health.last_empty = datetime.now(UTC)
                empty_sources.append(name)
                logger.warning(
                    "calendar source returned empty (no error), continuing failover",
                    source=name,
                )
                continue
            # VERIFIED non-empty success.
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.last_error = None
            if failed_sources:
                await self._emit_alert(
                    f"✅ Calendar fallback recovered: {' → '.join(failed_sources)} gagal → {name} OK"
                )
            return events
        # Every source either errored or returned empty.
        if empty_sources:
            # At least one source returned empty WITHOUT error: not all errored.
            # Loud alert (possible schema drift OR a genuinely quiet window). Return
            # [] WITHOUT recording a verified success, so the staleness gate stays
            # fail-CLOSED rather than treating this as "fresh with zero events".
            logger.warning(
                "ALL calendar sources returned EMPTY",
                empty_sources=empty_sources,
                failed_sources=failed_sources,
            )
            await self._emit_alert(
                "🚨 CALENDAR: SEMUA sumber EMPTY (schema drift? / quiet window) — "
                f"empty={empty_sources} failed={failed_sources}"
            )
            return []
        # Every source ERRORED (none returned even empty).
        await self._emit_alert("🚨 CALENDAR: SEMUA sumber gagal (total staleness)")
        raise ProviderError("all calendar sources unavailable")

    def health_snapshot(self) -> dict[str, CalendarSourceHealth]:
        return dict(self._health)

    def freshest_last_success(self) -> datetime | None:
        """Most recent VERIFIED (non-empty) success across sources.

        ``last_success`` is only set on a non-empty fetch (defect B1), so this
        reflects verified freshness. The staleness gate keys off this signal,
        keeping it fail-CLOSED on all-empty cycles.
        """
        times = [h.last_success for h in self._health.values() if h.last_success]
        return max(times) if times else None

    def freshest_nonempty_success(self) -> datetime | None:
        """Alias for :meth:`freshest_last_success` with an explicit name.

        Both reflect the last VERIFIED non-empty fetch. Prefer this name in new
        callers; an all-empty cycle never advances it.
        """
        return self.freshest_last_success()

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
