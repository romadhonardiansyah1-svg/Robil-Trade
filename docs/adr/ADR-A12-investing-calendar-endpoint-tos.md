# ADR-A12 — Investing.com Public JSON as Primary Calendar Source (Endpoint + ToS)

- **Status:** Accepted
- **Date:** post-audit
- **Decision owner:** Robil Trade engineering
- **Related:** GR-07b, FR-SCH-07, ADR-A10, ADR-009, `data/investing_calendar.py`, `data/composite_calendar.py`

## Context
Finnhub `/calendar/economic` is paid-only and returns HTTP 403 on the free tier.
Without economic events the `economic_events` table stays empty, and GR-07b
fail-CLOSES — rejecting every non-crypto signal. A keyless calendar source is
therefore required for the P0 deployment.

The investing.com public JSON endpoint (`api.investing.com` economic-calendar)
is undocumented and unofficial. Its Terms of Service around programmatic access
are a grey area, and the response shape can drift without notice.

## Decision
Use the investing.com public JSON endpoint as the **PRIMARY** calendar source,
behind the `CalendarProvider` ABC, with `NasdaqCalendarProvider` as **secondary**
and `StaticCalendarProvider` as the **last-resort** source. The
`CompositeCalendarProvider` tries sources in order and falls through on failure,
so no single source's ToS or outage risk can disable the fail-CLOSE gate.

- Parsing is defensive: per-row `try/except` and tolerant datetime handling.
- No Selenium or browser automation.
- Independent re-implementation — no third-party GPL/AGPL code (per ADR-A10).

## Consequences
ToS risk is mitigated by:
- (a) low request volume — calendar sync runs 2×/day,
- (b) browser-like request headers,
- (c) a config-driven swap to a paid tier (Finnhub paid / Trading Economics) if
  uptime or ToS require it.

Endpoint drift is handled by defensive parsing, the composite fallback chain, and
a recorded test fixture exercising the expected payload shape.

## Alternatives considered (rejected)
- **Paid Finnhub from day one** — rejected: cost, not needed for P0.
- **Selenium scraping** — rejected: fragile and worse from a ToS standpoint.
- **No calendar** — rejected: GR-07b would block all non-crypto signals, which is
  unacceptable.
