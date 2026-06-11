"""News blackout filter (PLAN §8.7, GR-07).

Blocks signals when high-impact economic events are imminent or just occurred.
Window: [now - after_min, now + before_min] where defaults are 15min/30min.

Events that are ALWAYS considered high-impact regardless of provider data:
- FOMC rate decision
- Non-Farm Payrolls (NFP)
- CPI (US)
- ECB rate decision
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog

from rtrade.core.timeutil import ensure_utc

logger = structlog.get_logger(__name__)

# Events always treated as high-impact (PLAN §8.7).
_ALWAYS_HIGH_KEYWORDS: set[str] = {
    "fomc",
    "federal funds rate",
    "fed interest rate",
    "nonfarm payrolls",
    "non-farm payrolls",
    "nfp",
    "consumer price index",
    "cpi",
    "ecb interest rate",
    "ecb rate decision",
    "ecb monetary policy",
}


def _is_always_high(event_name: str) -> bool:
    """Check if an event is in the always-high list."""
    name_lower = event_name.lower()
    return any(keyword in name_lower for keyword in _ALWAYS_HIGH_KEYWORDS)


def check_news_blackout(
    events: list[dict[str, object]],
    related_currencies: list[str],
    now: datetime,
    *,
    before_min: int = 30,
    after_min: int = 15,
) -> tuple[bool, str | None]:
    """Check if we are in a news blackout window.

    Args:
        events: List of dicts with keys: event, currency, impact, event_time.
        related_currencies: Currencies that affect this instrument.
        now: Current UTC time.
        before_min: Minutes before event to start blackout.
        after_min: Minutes after event to end blackout.

    Returns:
        (is_blocked, reason): True if signal should be blocked, with reason.
    """
    now = ensure_utc(now)
    window_start = now - timedelta(minutes=after_min)
    window_end = now + timedelta(minutes=before_min)

    related_upper = {c.upper() for c in related_currencies}

    for event in events:
        currency = str(event.get("currency", "")).upper()
        if currency not in related_upper:
            continue

        impact = str(event.get("impact", "low")).lower()
        event_name = str(event.get("event", ""))

        # Only block on high-impact events (or always-high).
        is_high = impact == "high" or _is_always_high(event_name)
        if not is_high:
            continue

        event_time = event.get("event_time")
        if event_time is None:
            continue
        if isinstance(event_time, str):
            from datetime import UTC

            event_time = datetime.fromisoformat(event_time).replace(tzinfo=UTC)

        event_time = ensure_utc(event_time)

        if window_start <= event_time <= window_end:
            reason = (
                f"GR-07: news blackout — {event_name} ({currency}) "
                f"at {event_time.isoformat()} is within "
                f"[now-{after_min}min, now+{before_min}min]"
            )
            logger.info("news blackout active", event_name=event_name, currency=currency)
            return True, reason

    return False, None
