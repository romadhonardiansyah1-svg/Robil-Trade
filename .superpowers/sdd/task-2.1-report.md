# Task 2.1 — DEFECT B1: Calendar fails over on empty + no false-fresh on all-empty

**File:** `src/rtrade/data/composite_calendar.py`
**Tests:** `tests/unit/test_calendar_providers.py` (extended)
**Branch:** `fix/audit-remediation`

## The fail-open scenario (B1)

`CompositeCalendarProvider.fetch_events` returned on the FIRST source that did
not raise — including when that source returned `[]`. An empty list recorded
`last_success = now`, reset `consecutive_failures`, and stopped failover. Two
safety consequences:

1. A source that returns `[]` on schema drift masked a later, healthy source
   that actually had the FOMC/NFP/CPI event — the working source was never
   consulted.
2. The downstream staleness gate keys off verified freshness
   (`CalendarSourceHealthRepo.freshest_success()`, populated from
   `health.last_success` in `sync_calendar`). A false `last_success` made the
   gate see "fresh, zero events" → the news blackout was never applied. Classic
   fail-OPEN on a safety path.

## Failover-on-empty design

In the source loop, a non-raising fetch is now split into two cases:

- **Non-empty list → VERIFIED success.** Set `last_success = now`, reset
  failures, clear `last_error`, emit the recovery alert if there were prior
  failures, and return the events. First non-empty source wins.
- **Empty list (no error) → NOT a success.** Record `last_empty = now`, append
  to a local `empty_sources` list, log a loud WARNING, and `continue` to the
  next source. `last_success` is left untouched, so a working source's events
  are always preferred over a broken source's `[]`.

`last_attempt` is still stamped every iteration (telemetry / liveness).

## All-empty vs genuinely-quiet — the freshness-accessor decision

After the loop, three terminal cases:

- **At least one source returned empty without error** (`empty_sources`
  non-empty): emit a loud all-empty alert
  (`🚨 CALENDAR: SEMUA sumber EMPTY (schema drift? / quiet window) …`), log a
  WARNING, and **return `[]` WITHOUT recording a verified success**. This keeps
  a genuinely quiet window from crashing while keeping the gate fail-CLOSED.
- **Every source ERRORED** (no empties): unchanged — emit
  `🚨 CALENDAR: SEMUA sumber gagal` and `raise ProviderError`.

**Freshness accessor decision:** `last_success` now means "last VERIFIED
(non-empty) success" by construction (it is only set on a non-empty fetch).
`freshest_last_success()` therefore already reflects verified freshness and is
documented as such. I added an explicit alias `freshest_nonempty_success()` for
new callers; both return the same value and neither advances on an all-empty
cycle.

**No scan.py change required.** The staleness gate consumes
`CalendarSourceHealthRepo.freshest_success()`, which `sync_calendar` populates
from `health.last_success`. Because `last_success` is now only set on verified
non-empty results, the gate is automatically fail-CLOSED on all-empty cycles —
the contract is preserved with zero changes to the consumer. This was the
minimal, lowest-risk option versus broadening the gate to a new accessor.

## Alert behavior

- Fallback-transition warning before trying the next source after a failure —
  unchanged.
- Recovery alert on a verified non-empty success after prior failures —
  unchanged.
- New: loud all-empty WARNING + alert when every source is empty/errored but at
  least one was empty.
- All-errored path still raises `ProviderError` with its existing alert.

## Verification

- **RED:** `test_composite_fails_over_on_empty_source` (got `[]`, expected
  FOMC) and `test_composite_all_empty_no_false_fresh` (no all-empty alert)
  failed as expected against the old code.
- **GREEN:** all calendar tests pass — `14 passed` in
  `tests/unit/test_calendar_providers.py`. New tests: failover-on-empty,
  all-empty-no-false-fresh, all-empty-does-not-raise; existing failover/raise/
  health tests still pass.
- **Full suite:** `813 passed, 7 skipped` in ~86s.
- **ruff:** `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- **mypy (strict):** `.venv\Scripts\mypy.exe src` → no issues in 129 files.

## Concerns / residual risk

- **Quiet window vs schema drift are still indistinguishable.** When every
  source returns `[]`, we cannot tell a genuinely quiet calendar window from a
  fleet-wide parse failure. We bias fail-CLOSED (no verified freshness →
  staleness gate trips), which is the safe choice, but it can cause false
  blackout/staleness during a legitimately quiet period.
- **Provider-level validation should raise on parse failure.** The real fix for
  the ambiguity above is at the source providers
  (`investing_calendar`, `nasdaq_calendar`, `finnhub_calendar`,
  `static_calendar`): a schema/parse failure should raise (counted as an error
  and alerted) rather than silently yielding `[]`. That converts "silent empty"
  into a loud failure and lets a healthy source win cleanly. Recommended as a
  follow-up task.
- `last_empty` is tracked on the health dataclass for telemetry but is not yet
  persisted by `CalendarSourceHealthRepo.upsert`; persisting it would improve
  observability of drift over time (optional follow-up).
