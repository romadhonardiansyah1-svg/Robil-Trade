"""Static last-resort economic-calendar provider (FR-CAL-03).

Zero network calls (NFR-SCALE-03). Memuat event high-impact berulang dari
config/static_calendar.json (refresh manual kuartalan). Tier terminal composite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
import hashlib
import json
from pathlib import Path

import structlog

from rtrade.core.errors import ProviderError
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)
_DEFAULT_PATH = Path("config/static_calendar.json")


class StaticCalendarProvider(CalendarProvider):
    """Serve high-impact events from a static JSON file (zero network).

    The file is loaded lazily on first :meth:`fetch_events` and cached. A
    missing or corrupt file (or any malformed event entry) RAISES
    ``ProviderError`` so the composite treats this tier as FAILED (→ failover /
    fail-CLOSED) rather than masking the breakage as an empty calendar. A valid
    file with no in-range events returns ``[]`` (legitimate empty).
    """

    def __init__(self, config_path: Path = _DEFAULT_PATH) -> None:
        self._path = config_path
        self._version = "unknown"
        self._events: list[tuple[datetime, str, str]] | None = None

    def _ensure_loaded(self) -> list[tuple[datetime, str, str]]:
        if self._events is not None:
            return self._events
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProviderError(f"Static calendar file unreadable: {self._path}: {exc}") from exc
        try:
            raw = json.loads(raw_text)
        except ValueError as exc:
            # JSONDecodeError (ValueError subclass): a corrupt file is a failure.
            raise ProviderError(f"Static calendar file corrupt JSON: {self._path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ProviderError(f"Static calendar: unexpected JSON shape in {self._path}")
        events_raw = raw.get("events", [])
        if not isinstance(events_raw, list):
            raise ProviderError(f"Static calendar: 'events' is not a list in {self._path}")
        parsed: list[tuple[datetime, str, str]] = []
        try:
            for e in events_raw:
                t = datetime.fromisoformat(str(e["time"]).replace("Z", "+00:00"))
                parsed.append((t, str(e["event"]), str(e["currency"])))
        except (KeyError, ValueError, TypeError) as exc:
            # A curated file is fail-CLOSED: one malformed entry corrupts the
            # whole file — raise rather than silently dropping events.
            raise ProviderError(
                f"Static calendar: malformed event entry in {self._path}: {exc}"
            ) from exc
        self._version = str(raw.get("version", "unknown"))
        self._events = parsed
        return parsed

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        loaded = self._ensure_loaded()
        out: list[DomainEvent] = []
        now = datetime.now(UTC)
        for event_time, event_name, currency in loaded:
            d = event_time.date()
            if start <= d <= end:
                eid = hashlib.sha256(
                    f"static:{event_name}:{event_time.isoformat()}:{currency}".encode()
                ).hexdigest()[:16]
                out.append(
                    DomainEvent(
                        event_id=eid,
                        event=event_name,
                        currency=currency,
                        impact="high",
                        event_time=event_time,
                        actual=None,
                        forecast=None,
                        previous=None,
                        fetched_at=now,
                    )
                )
        logger.info(
            "static calendar served",
            start=start.isoformat(),
            end=end.isoformat(),
            total=len(out),
            version=self._version,
        )
        return out

    async def close(self) -> None:
        return None
