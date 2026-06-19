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

from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)
_DEFAULT_PATH = Path("config/static_calendar.json")


class StaticCalendarProvider(CalendarProvider):
    """Serve high-impact events from a static JSON file (zero network)."""

    def __init__(self, config_path: Path = _DEFAULT_PATH) -> None:
        self._path = config_path
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        self._version = str(raw.get("version", "unknown"))
        self._events: list[tuple[datetime, str, str]] = []
        for e in raw.get("events", []):
            t = datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
            self._events.append((t, str(e["event"]), str(e["currency"])))

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        out: list[DomainEvent] = []
        now = datetime.now(UTC)
        for event_time, event_name, currency in self._events:
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
