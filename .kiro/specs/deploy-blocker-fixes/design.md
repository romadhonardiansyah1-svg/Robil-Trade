# Deploy Blocker Fixes Bugfix Design

## Overview

Robil Trade (signal-only trading bot, Python 3.12) is currently not deployable. A direct
verification run of the quality gate (`ruff`, `mypy --strict`, `pytest tests/unit`) plus a
code read surfaced five defect groups that block deploy. This design formalizes each defect
as a bug condition `C(X)`, defines the desired post-fix behavior `P(result)`, and documents
the non-buggy behavior `¬C(X)` that must be preserved.

The five defects and their fix strategy at a glance:

- **BUG 1 (BLOCKER) — selftest crash.** `run_guardrail_selftest()` builds known-bad
  `SignalCandidate` instances to prove each gate rejects them. Since GR-02/03/04 are now
  enforced in the Pydantic `model_validator` at construction time, building the bad
  candidate raises `ValidationError` before `run_gate` is ever reached, crashing the worker
  at startup. Fix: make the selftest construct bad candidates without tripping the
  construction-time validators, scoped strictly to the selftest module (never the
  production path), so GI-5 holds.
- **BUG 2 (BLOCKER) — alert regression.** A "PLAN v2" commit overwrote `scheduler/jobs.py`
  and dropped the rate-limit suppression + 2-hour cooldown logic, including the
  `_last_alert_at` state. Fix: restore typed error handling (suppress `RateLimitExceeded`
  alerts) and the once-then-cooldown alert path with `_last_alert_at`.
- **BUG 3 (HIGH) — scan scheduling burst.** `build_scan_schedules()` packs all TwelveData
  H1 instruments onto `minute="0"` staggered by only 5 seconds, draining the free bucket.
  Fix: spread H1 instruments across minutes `["0","10","20","30"]` (all `second="30"`) and
  move H4 onto `minute="5"`.
- **BUG 4 (MEDIUM) — mypy --strict red.** Three type errors in the calendar modules. Fix:
  align the `params` type to what `httpx` accepts and pass a correctly typed argument to
  `_normalize_impact`. Runtime behavior unchanged.
- **BUG 5 (MEDIUM) — wasteful incremental ingest.** `_ingest_incremental()` always calls
  the provider even when the latest candle is still fresh (age < 1 bar), wasting credits and
  worsening BUG 3. Fix: short-circuit and return `0` when the watermark is fresh.

All fixes are minimal and targeted. No guardrail is weakened and no project invariant is
touched: signal-only (no order/broker), fail-CLOSE calendar
(`calendar.fail_open_when_stale=false`), risk floors (GR-03 RR≥1.5, GR-04 SL∈[0.5,3.0]×ATR,
GR-05 risk≤2%), GI-5 (no `model_construct` in production), deterministic tests
(freezegun/respx, no live network), and `llm.enabled=false`.

## Glossary

- **Bug_Condition (C)**: The input/condition set that triggers a defect. Each of the five
  bugs has its own sub-condition `C1..C5`; the overall bug condition is their union.
- **Property (P)**: The desired behavior on inputs satisfying `C` after the fix.
- **Preservation**: Behavior on inputs where `¬C` holds, which must remain identical to the
  current (correct) behavior.
- **F / F'**: The original (unfixed) function vs. the fixed function.
- **`run_guardrail_selftest()`**: Startup integrity check in `src/rtrade/guardrails/selftest.py`
  that builds illegal candidates, runs `run_gate`, and returns a `list[str]` of problems
  (empty = healthy).
- **`model_validator` (GR-02/03/04)**: The `check_direction_and_rr` validator on
  `SignalCandidate` and `check_invariants` on `LevelSet` in `src/rtrade/signals/schemas.py`
  that reject illegal levels at construction time.
- **GI-5**: Invariant forbidding `model_construct` (validator bypass) anywhere on the
  production signal path.
- **`scan_job()`**: Scheduler job in `src/rtrade/scheduler/jobs.py` that runs one scan and,
  on repeated failures, sends a Telegram alert.
- **`_fail_counts` / `_last_alert_at`**: Module-level state in `jobs.py` tracking consecutive
  failures per `symbol:timeframe` key and the last alert timestamp per key (cooldown).
- **`RateLimitExceeded`**: `ProviderError` subclass in `src/rtrade/core/errors.py` raised when
  a provider/local bucket is exhausted.
- **`build_scan_schedules()`**: Cron-schedule builder in `src/rtrade/scheduler/main.py` that
  emits `(symbol, tf, cron_kwargs)` per instrument×timeframe.
- **`_ingest_incremental()`**: Incremental candle ingestion in `src/rtrade/pipeline/scan.py`
  that fetches only missing candles from the watermark.
- **`_normalize_impact`**: Helper in `src/rtrade/data/nasdaq_calendar.py` with signature
  `(raw_impact: str | int, event_name: str) -> str`.

## Bug Details

### Bug Condition

The bug manifests across five independent code paths. The combined bug condition is the
union of the five sub-conditions below. The fixed code must satisfy the corresponding
property for each, while leaving all other inputs untouched.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input — one of {SelftestRun, ScanFailure, ScheduleRequest, MypyCheck, IngestRequest}
  OUTPUT: boolean

  // C1 — BUG 1: selftest crashes building an illegal candidate
  IF input is SelftestRun
     AND constructing a known-bad SignalCandidate raises ValidationError
     AND that exception escapes run_guardrail_selftest()
  THEN RETURN true

  // C2 — BUG 2: rate-limit failures spam Telegram (no type discrimination)
  IF input is ScanFailure
     AND input.error IS_A RateLimitExceeded
     AND consecutive_failures >= _ALERT_THRESHOLD
     AND an alert is sent for that rate-limit error
  THEN RETURN true

  // C2b — BUG 2: non-rate-limit failures lack once-then-cooldown (state removed)
  IF input is ScanFailure
     AND input.error IS NOT RateLimitExceeded
     AND (jobs has no _last_alert_at  OR  alert is re-sent within cooldown window)
  THEN RETURN true

  // C3 — BUG 3: TwelveData H1 instruments packed on the same minute
  IF input is ScheduleRequest
     AND provider is TwelveData
     AND (multiple H1 entries share minute="0"  OR  H4 entry uses minute="0")
  THEN RETURN true

  // C4 — BUG 4: calendar modules fail mypy --strict
  IF input is MypyCheck on {investing_calendar.py, nasdaq_calendar.py}
     AND a type error is reported on the httpx .get(params=...) call
         OR on the _normalize_impact(...) call
  THEN RETURN true

  // C5 — BUG 5: incremental ingest fetches a still-fresh watermark
  IF input is IngestRequest
     AND latest candle exists
     AND age(latest.ts, now) < 1 * timeframe_duration(tf)
     AND the provider is still called (fetch_ohlcv invoked)
  THEN RETURN true

  RETURN false
END FUNCTION
```

### Examples

- **BUG 1:** `run_worker()` → `run_guardrail_selftest()` → `_make_candidate(action=BUY,
  levels=LevelSet(entry=2000, sl=2010, tp=2020, atr=5))`. Expected: candidate built, fed to
  `run_gate`, gate rejects it, `problems` stays empty. Actual: `SignalCandidate(...)` raises
  `ValidationError("GR-02: BUY requires SL < entry < TP")`, escaping the function → worker
  exits at startup.
- **BUG 2 (rate-limit):** `scan_job("USDJPY","1h")` fails 4× with `RateLimitExceeded`.
  Expected: no Telegram alert, `_fail_counts["USDJPY:1h"] == 4`. Actual: alert fired on the
  3rd+ failure, spamming the channel.
- **BUG 2 (cooldown):** `scan_job("USDJPY","1h")` fails 4× with `RuntimeError("database
  unavailable")`. Expected: exactly one alert containing "database unavailable", further
  alerts suppressed for the cooldown window; `jobs._last_alert_at` exists. Actual:
  `jobs._last_alert_at` missing → test fixture `_reset_job_state` raises `AttributeError`.
- **BUG 3:** four TwelveData H1 instruments (XAUUSD, EURUSD, GBPUSD, USDJPY). Expected:
  `minute` values `["0","10","20","30"]`, all `second="30"`; H4 on `minute="5"`,
  `hour="0,4,8,12,16,20"`. Actual: all on `minute="0"` staggered by 5s → burst → 429.
- **BUG 4:** `mypy --strict` reports `investing_calendar.py:130` and `nasdaq_calendar.py:124`
  (`params: dict[str, object]` not accepted by `httpx.AsyncClient.get`) and
  `nasdaq_calendar.py:169` (`_normalize_impact` got `Any | None`, expected `str | int`).
  Expected: zero errors. Actual: 3 errors.
- **BUG 5:** H1, latest candle `ts=09:00`, `now=10:00` (age = 1h = exactly the in-progress
  bar boundary; a fresher `ts=09:30`/`now=10:00` is clearly < 1 bar). Expected: return `0`,
  no `fetch_ohlcv`. Actual: provider called, wasting a credit.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `run_guardrail_selftest()` still detects a genuinely broken gate: for every illegal
  condition tested (GR-02..GR-13 and the valid-candidate regression check), a problem entry
  is recorded if the gate fails to reject — `tests/unit/test_guardrail_selftest.py` stays
  green.
- Construction-time validation of `SignalCandidate`/`LevelSet` on the production path is
  unchanged; illegal candidates are still rejected at construction and `model_construct` is
  never used in production (GI-5).
- Guardrail risk floors are untouched: GR-03 RR≥1.5, GR-04 SL∈[0.5,3.0]×ATR, GR-05 risk≤2%.
- On scan success, `scan_job()` resets `_fail_counts[key]=0` and sends no alert.
- `build_scan_schedules()` still emits exactly one entry per instrument×timeframe (4 entries
  for 2 instruments × 2 TFs), and still staggers seconds for non-TwelveData instruments.
- `_ingest_incremental()` still backfills on first run (`since = now − 120 days`,
  `limit = 500`, one provider call) and still fetches incrementally for a stale watermark
  (`since = watermark − 2 bars`, `limit = 10`, one provider call).
- Calendar modules produce functionally identical events (parsing, impact normalization,
  429/error handling); only type annotations/casts change, not runtime logic.
- Non-crypto stale calendar still fails CLOSE (`fail_open_when_stale=false` unchanged);
  GR-07b still rejects.
- System stays signal-only, `llm.enabled` stays false, tests stay deterministic.

**Scope:**
All inputs where `isBugCondition` is false must be completely unaffected by this fix.
This includes: healthy selftest gates, successful scans, non-rate-limit failures below
threshold, non-TwelveData schedules, calendar runtime behavior, first-run and stale-watermark
ingestion, and every guardrail decision.

## Hypothesized Root Cause

**BUG 1 — selftest crash**
1. **Validation moved to construction time.** `SignalCandidate.check_direction_and_rr` and
   `LevelSet.check_invariants` now enforce GR-02/03/04 in `model_validator(mode="after")`.
   The selftest's `_make_candidate(...)` calls the real constructor, so a known-bad candidate
   raises `ValidationError` before `run_gate` runs.
2. **No isolation between "schema-level" and "gate-level" rejection.** The selftest assumes
   it can hand an illegal object to `run_gate`; that assumption broke when schema validation
   tightened.

**BUG 2 — alert regression**
1. **Lost typed error discrimination.** Current `scan_job()` treats every `Exception`
   identically; it no longer special-cases `RateLimitExceeded`, so bucket-exhaustion bursts
   trigger alerts.
2. **Lost cooldown state.** `_last_alert_at` was removed from `jobs.py`, so there is no
   once-then-cooldown gating and the reference test fixture `AttributeError`s.

**BUG 3 — scheduling burst**
1. **Minute collision.** All H1 entries use `minute="0"`; the only spread is a 5-second
   `second` stagger, so 4 instruments fire within ~15s of the hour boundary.
2. **H4 collision.** H4 also uses `minute="0"`, stacking on top of the H1 burst.

**BUG 4 — mypy --strict**
1. **`params` typed too widely.** `dict[str, object]` is not assignable to httpx's expected
   `QueryParamTypes`; httpx accepts `str | int | float | bool | None` (and sequences) as
   values, not bare `object`.
2. **Loosely typed dict lookup.** `row_dict.get("impact", row_dict.get("importance", 1))`
   yields `Any | None`, which violates `_normalize_impact(raw_impact: str | int, ...)`.

**BUG 5 — wasteful ingest**
1. **No freshness short-circuit.** `_ingest_incremental()` computes a `since` window and
   always calls `ingest_candles` even when `now − latest.ts < 1` bar, so an in-progress bar
   triggers a redundant fetch.

## Correctness Properties

Property 1: Bug Condition (BUG 1) — Selftest Returns Without Crashing

_For any_ selftest run on healthy code (C1 holds in the unfixed code), the fixed
`run_guardrail_selftest()` SHALL return a `list[str]` without raising `pydantic.ValidationError`,
while still exercising the GR-02/GR-03/GR-04 gate-effectiveness checks; and `run_worker()`
SHALL continue startup when the list is empty and SHALL `raise SystemExit(1)` when it is
non-empty.

**Validates: Requirements 2.1, 2.2**

Property 2: Bug Condition (BUG 2) — Rate-Limit Alerts Suppressed

_For any_ sequence of `scan_job` failures caused by `RateLimitExceeded` (C2 holds), the fixed
code SHALL NOT send any Telegram alert for those failures, while still incrementing
`_fail_counts[key]` for each failure (e.g. `_fail_counts["USDJPY:1h"] == 4` after 4 failures).

**Validates: Requirements 2.3**

Property 3: Bug Condition (BUG 2) — Non-Rate-Limit Alert Once Then Cooldown

_For any_ sequence of `scan_job` failures caused by a non-`RateLimitExceeded` error after the
threshold is reached (C2b holds), the fixed code SHALL send exactly one Telegram alert
containing the error detail (e.g. "database unavailable"), then suppress further alerts for
the cooldown window; the module state attribute `_last_alert_at` SHALL exist on `jobs`.

**Validates: Requirements 2.4**

Property 4: Bug Condition (BUG 3) — TwelveData Schedules Spread Across Minutes

_For any_ `build_scan_schedules()` call over the four TwelveData H1 instruments
(XAUUSD, EURUSD, GBPUSD, USDJPY) (C3 holds), the fixed code SHALL assign `minute` values
`["0","10","20","30"]` with all `second == "30"`; and any H4 entry SHALL use `minute == "5"`
with `hour == "0,4,8,12,16,20"`.

**Validates: Requirements 2.5, 2.6**

Property 5: Bug Condition (BUG 4) — Calendar Modules Type-Clean

_For any_ `mypy --strict` run over `investing_calendar.py` and `nasdaq_calendar.py` (C4 holds),
the fixed code SHALL report zero type errors on the `httpx.AsyncClient.get(params=...)` calls
and on the `_normalize_impact(...)` call (the argument SHALL be `str | int`, not `Any | None`).

**Validates: Requirements 2.7, 2.8, 2.9**

Property 6: Bug Condition (BUG 5) — Fresh Watermark Skips Provider

_For any_ `_ingest_incremental()` call where the latest candle is fresher than one bar
(C5 holds), the fixed code SHALL return `0` and SHALL NOT call the provider (no `fetch_ohlcv`
/ `ingest_candles` invocation).

**Validates: Requirements 2.10**

Property 7: Preservation — Selftest Still Detects Broken Gates and Invariants Hold

_For any_ input where the bug condition does NOT hold, the fixed selftest SHALL produce the
same result as the original intent: it still records a problem for every gate that fails to
reject an illegal candidate, the production path still rejects illegal candidates at
construction via `model_validator`, `model_construct` is never used in production (GI-5), and
GR-03/GR-04/GR-05 risk floors are unchanged.

**Validates: Requirements 3.1, 3.2, 3.3**

Property 8: Preservation — Scan Success and Below-Threshold/Stagger Behavior

_For any_ input where the bug condition does NOT hold, the fixed code SHALL behave exactly as
before: successful scans reset `_fail_counts[key]=0` and send no alert; `build_scan_schedules`
still emits 4 entries for 2 instruments × 2 TFs and still staggers seconds for non-TwelveData
instruments.

**Validates: Requirements 3.4, 3.5, 3.6**

Property 9: Preservation — Ingestion First-Run / Stale Watermark and Calendar Runtime

_For any_ input where the bug condition does NOT hold, the fixed code SHALL preserve existing
behavior: first-run ingestion backfills (`since = now − 120 days`, `limit = 500`, one call);
stale-watermark ingestion fetches incremental (`since = watermark − 2 bars`, `limit = 10`,
one call); calendar parsing/normalization/error handling is functionally identical (only
types change); non-crypto stale calendar fails CLOSE; system stays signal-only with
`llm.enabled=false` and deterministic tests.

**Validates: Requirements 3.7, 3.8, 3.9, 3.10, 3.11**

## Fix Implementation

Assuming the root-cause analysis is correct, the following targeted changes are required.

### BUG 1 — `src/rtrade/guardrails/selftest.py`

**Function**: `_make_candidate` / `run_guardrail_selftest`

**Specific Changes**:
1. **Isolate illegal-candidate construction inside the selftest only.** Provide a
   selftest-local way to build a candidate that bypasses the construction-time validators
   (e.g. `SignalCandidate.model_construct(...)` for the *known-bad* cases) so the object can
   reach `run_gate`. This bypass lives exclusively in `selftest.py` and is never imported or
   used on the production signal path — satisfying clause 2.1 and preserving GI-5 (3.2).
2. **Keep the valid candidate constructed normally.** The `good` candidate and the GR-10
   mutation pair continue to use the real constructor so the regression check still proves a
   valid candidate passes.
3. **Alternative considered:** test the schema validators separately (assert that
   constructing the bad candidate raises) and only feed gate-only conditions to `run_gate`.
   Either approach is acceptable; the chosen approach must keep all existing problem-detection
   coverage (3.1) and must not touch production code paths.

### BUG 2 — `src/rtrade/scheduler/jobs.py`

**Function**: `scan_job` / `_send_failure_alert`

**Specific Changes**:
1. **Reintroduce cooldown state.** Add module-level `_last_alert_at: dict[str, datetime] = {}`
   and a cooldown constant (2 hours, e.g. `_ALERT_COOLDOWN = timedelta(hours=2)`).
2. **Discriminate error type in the `except` block.** Catch `RateLimitExceeded` (or check
   `isinstance(exc, RateLimitExceeded)`) and, while still incrementing `_fail_counts[key]`,
   skip the alert entirely for rate-limit errors (2.3).
3. **Once-then-cooldown for other errors.** For non-rate-limit errors at/above
   `_ALERT_THRESHOLD`, send the alert only if `key` has no recent `_last_alert_at` within the
   cooldown window; on send, record `_last_alert_at[key] = now` (2.4). Include the error
   detail in the message.
4. **Preserve success reset.** Keep `_fail_counts[key] = 0` on success (3.4).

### BUG 3 — `src/rtrade/scheduler/main.py`

**Function**: `build_scan_schedules`

**Specific Changes**:
1. **Spread TwelveData H1 across minutes.** For TwelveData instruments, assign per-instrument
   minutes from `["0","10","20","30"]` (by index) with `second="30"` instead of all-on-`"0"`
   (2.5).
2. **Move H4 off the H1 minute.** Use `minute="5"` with `hour="0,4,8,12,16,20"` for H4 (2.6).
3. **Preserve non-TwelveData stagger.** Keep the existing second-stagger logic for
   non-TwelveData instruments so `test_seconds_staggered` stays green (3.6), and keep one
   entry per instrument×TF (3.5).

### BUG 4 — `src/rtrade/data/investing_calendar.py`, `src/rtrade/data/nasdaq_calendar.py`

**Function**: `_get` / `fetch_events` / `_normalize_impact` call site

**Specific Changes**:
1. **Align `params` type.** Change the `params` annotation from `dict[str, object]` to a type
   httpx accepts (e.g. `dict[str, str]`, or `httpx.QueryParams` / the `QueryParamTypes`
   alias). All current values (`isoformat()` strings, the api_key string, `"60"`) are already
   strings, so this is a pure annotation change with no runtime effect (2.7, 2.8, 3.10).
2. **Type the `_normalize_impact` argument.** Coerce/cast the looked-up impact value to
   `str | int` before passing it (e.g. wrap in `str(...)` or a typed local), removing the
   `Any | None` error without changing normalization output (2.9, 3.10).

### BUG 5 — `src/rtrade/pipeline/scan.py`

**Function**: `_ingest_incremental`

**Specific Changes**:
1. **Add a freshness short-circuit.** After loading `latest`, if `latest is not None` and
   `now − ensure_utc(latest.ts) < timeframe_duration(tf)` (one bar), return `0` immediately
   without calling `ingest_candles` (2.10).
2. **Preserve the two existing branches.** First-run (`latest is None`) backfill and
   stale-watermark incremental fetch are unchanged (3.7, 3.8).

## Testing Strategy

### Validation Approach

Two phases. First, surface counterexamples that demonstrate each bug on the UNFIXED code to
confirm (or refute) the root-cause analysis. Then verify the fix makes the buggy inputs
satisfy their property AND that all non-buggy inputs behave exactly as before (preservation).
The whole-suite acceptance is: `ruff check`, `ruff format --check`, `mypy --strict`, and
`pytest tests/unit` all green (0 failed / 0 error), plus the worker starts without crashing.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples demonstrating each bug BEFORE implementing fixes. Confirm
or refute the hypothesized root cause. If refuted, re-hypothesize.

**Test Plan**: Run targeted checks against the unfixed code for each bug and observe the
failure.

**Test Cases**:
1. **BUG 1**: Call `run_guardrail_selftest()` directly and observe an uncaught
   `pydantic.ValidationError` (will fail on unfixed code).
2. **BUG 2 (rate-limit)**: Run the existing `test_scan_job_suppresses_rate_limit_telegram_alerts`
   — it errors because `_last_alert_at` is missing and/or an alert is sent (will fail on
   unfixed code).
3. **BUG 2 (cooldown)**: Run `test_scan_job_alerts_non_rate_limit_once_until_cooldown` — fails
   on the missing `_last_alert_at` attribute (will fail on unfixed code).
4. **BUG 3**: Assert TwelveData H1 minutes equal `["0","10","20","30"]` — fails because all are
   `"0"` (will fail on unfixed code).
5. **BUG 4**: Run `mypy --strict` on both calendar modules and observe the 3 reported errors
   (will fail on unfixed code).
6. **BUG 5**: Call `_ingest_incremental` with a fresh watermark and a spy provider; assert the
   provider was not called — fails because it is called (will fail on unfixed code).

**Expected Counterexamples**:
- BUG 1: `ValidationError("GR-02: BUY requires SL < entry < TP")` escaping the function.
- BUG 2: `AttributeError: module 'rtrade.scheduler.jobs' has no attribute '_last_alert_at'`
  and/or a non-empty `alerts` list for rate-limit failures.
- BUG 3: `minute` list `["0","0","0","0"]` instead of the staggered minutes.
- BUG 4: three `mypy` errors at `investing_calendar.py:130`, `nasdaq_calendar.py:124`,
  `nasdaq_calendar.py:169`.
- BUG 5: a recorded `fetch_ohlcv` call when the watermark is fresh.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces
the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := fixedFunction(input)
  ASSERT expectedBehavior(result)   // Property 1..6 for the matching sub-condition
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function
produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT originalFunction(input) = fixedFunction(input)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation, especially for
`build_scan_schedules` (random instrument/TF combinations) and `_ingest_incremental` (random
watermark ages), because it generates many cases automatically, catches edge cases manual
tests miss, and gives strong guarantees that non-buggy behavior is unchanged.

**Test Plan**: Observe behavior on the UNFIXED code for non-bug inputs (successful scans,
below-threshold failures, non-TwelveData schedules, first-run/stale ingestion, calendar
runtime), then write tests capturing that behavior so the fix cannot regress it.

**Test Cases**:
1. **Scan success preservation**: success resets `_fail_counts[key]=0` and sends no alert (3.4).
2. **Schedule count/stagger preservation**: 4 entries for 2 instruments × 2 TFs; non-TwelveData
   seconds staggered (3.5, 3.6).
3. **Ingestion preservation**: first-run backfill (`since=now−120d`, `limit=500`, one call) and
   stale-watermark incremental (`since=watermark−2 bars`, `limit=10`, one call) (3.7, 3.8).
4. **Selftest detection preservation**: `test_guardrail_selftest.py` still green; a deliberately
   broken gate still produces a problem entry (3.1, 3.2, 3.3).
5. **Calendar runtime preservation**: parsing/normalization/429 handling unchanged on fixed
   modules (3.10); non-crypto stale still fails CLOSE (3.9).

### Unit Tests

- BUG 1: `run_guardrail_selftest()` returns `[]` on healthy code; returns a non-empty list when
  a gate is monkeypatched to wrongly pass; `run_worker` raises `SystemExit(1)` on non-empty.
- BUG 2: rate-limit suppression test and once-then-cooldown test (the two existing reference
  tests in `tests/unit/test_scheduler_jobs.py`), plus a cooldown-expiry case.
- BUG 3: assert H1 minutes `["0","10","20","30"]`/`second="30"` and H4 `minute="5"`/
  `hour="0,4,8,12,16,20"` for TwelveData; assert non-TwelveData stagger preserved.
- BUG 4: covered by `mypy --strict` in the gate; add a small runtime test asserting calendar
  events parse identically.
- BUG 5: fresh-watermark returns `0` with no provider call; first-run and stale branches still
  call the provider once.

### Property-Based Tests

- `build_scan_schedules`: generate random instrument lists (mixed providers/TFs) and assert the
  invariants — one entry per instrument×TF, TwelveData H1 minutes drawn from
  `["0","10","20","30"]`, non-TwelveData seconds staggered.
- `_ingest_incremental`: generate random `(latest.ts, now, tf)`; assert provider is called iff
  watermark age ≥ 1 bar (and not called when fresh), and the correct `since`/`limit` are used
  per branch.

### Integration Tests

- Worker startup: `run_worker()` completes the selftest and proceeds to scheduler start on
  healthy code (no crash), and fail-closes with `SystemExit(1)` when the selftest reports a
  problem.
- Full quality gate: `ruff check`, `ruff format --check`, `mypy --strict`, `pytest tests/unit`
  all green — the overall acceptance condition.
- Alert flow: simulated repeated rate-limit vs. non-rate-limit failures exercise suppression
  and once-then-cooldown end to end through `scan_job`.
