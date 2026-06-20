# Implementation Plan

This plan follows the exploratory bugfix workflow: write exploration tests that FAIL on the
unfixed code (proving each bug exists), write preservation tests that PASS on the unfixed code
(capturing baseline behavior), then apply minimal fixes and verify. Property numbers map
directly to the Correctness Properties in `design.md`.

## Exploration Tests (write BEFORE any fix)

- [x] 1. Write bug condition exploration test for BUG 1 (selftest crash)
  - **Property 1: Bug Condition** - Selftest Returns Without Crashing
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior - it will validate the fix when it passes after implementation
  - **GOAL**: Surface a counterexample demonstrating the selftest crashes at construction time
  - **Scoped PBT Approach**: This is a deterministic bug; scope the property to the concrete case of calling `run_guardrail_selftest()` on the current healthy code
  - Test that `run_guardrail_selftest()` returns a `list[str]` (empty on healthy code) WITHOUT raising `pydantic.ValidationError` (from Bug Condition C1 in design)
  - The assertion should match Property 1: returns a list, does not crash, still exercises GR-02/GR-03/GR-04 gate checks
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexample found (expected: `ValidationError("GR-02: BUY requires SL < entry < TP")` escaping the function at `src/rtrade/guardrails/selftest.py:53`)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. Write bug condition exploration test for BUG 2 (rate-limit alert spam)
  - **Property 2: Bug Condition** - Rate-Limit Alerts Suppressed
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface a counterexample where repeated `RateLimitExceeded` failures send Telegram alerts
  - **Scoped PBT Approach**: Scope the property to a sequence of `scan_job("USDJPY","1h")` failures raising `RateLimitExceeded` (from Bug Condition C2 in design)
  - Test that NO Telegram alert is sent for `RateLimitExceeded` failures, while `_fail_counts["USDJPY:1h"] == 4` after 4 failures (assertion matches Property 2)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexample (expected: a non-empty `alerts` list for rate-limit failures, and/or `AttributeError` on missing `_last_alert_at`)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.3, 2.3_

- [x] 3. Write bug condition exploration test for BUG 2 (non-rate-limit cooldown)
  - **Property 3: Bug Condition** - Non-Rate-Limit Alert Once Then Cooldown
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface a counterexample where the cooldown state is missing / alerts re-send
  - **Scoped PBT Approach**: Scope to a sequence of `scan_job("USDJPY","1h")` failures raising `RuntimeError("database unavailable")` after the threshold (from Bug Condition C2b in design)
  - Test that exactly ONE Telegram alert is sent containing the error detail ("database unavailable"), further alerts suppressed within the cooldown window, and `jobs._last_alert_at` exists (assertion matches Property 3)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexample (expected: `AttributeError: module 'rtrade.scheduler.jobs' has no attribute '_last_alert_at'` via fixture `_reset_job_state`)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.4, 2.4_

- [x] 4. Write bug condition exploration test for BUG 3 (schedule burst)
  - **Property 4: Bug Condition** - TwelveData Schedules Spread Across Minutes
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface a counterexample where all TwelveData H1 instruments share the same minute
  - **Scoped PBT Approach**: Scope to `build_scan_schedules()` over the four TwelveData H1 instruments (XAUUSD, EURUSD, GBPUSD, USDJPY) (from Bug Condition C3 in design)
  - Test that H1 `minute` values equal `["0","10","20","30"]` with all `second == "30"`, and any H4 entry uses `minute == "5"` with `hour == "0,4,8,12,16,20"` (assertion matches Property 4)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexample (expected: `minute` list `["0","0","0","0"]` instead of the staggered minutes)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.5, 1.6, 2.5, 2.6_

- [x] 5. Write bug condition exploration test for BUG 4 (mypy --strict red)
  - **Property 5: Bug Condition** - Calendar Modules Type-Clean
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface the three reported type errors on the calendar modules
  - **Scoped PBT Approach**: This is a deterministic static-analysis check; scope to running `mypy --strict` over `investing_calendar.py` and `nasdaq_calendar.py` (from Bug Condition C4 in design)
  - Assert zero type errors on the `httpx.AsyncClient.get(params=...)` calls and on the `_normalize_impact(...)` call (argument should be `str | int`, not `Any | None`) (assertion matches Property 5)
  - Run `mypy --strict` on UNFIXED code
  - **EXPECTED OUTCOME**: Check FAILS (this is correct - it proves the bug exists)
  - Document the counterexamples (expected: errors at `investing_calendar.py:130`, `nasdaq_calendar.py:124`, `nasdaq_calendar.py:169`)
  - Mark task complete when the failing mypy output is documented
  - _Requirements: 1.7, 1.8, 1.9, 2.7, 2.8, 2.9_

- [x] 6. Write bug condition exploration test for BUG 5 (wasteful incremental ingest)
  - **Property 6: Bug Condition** - Fresh Watermark Skips Provider
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **GOAL**: Surface a counterexample where a still-fresh watermark triggers a provider fetch
  - **Scoped PBT Approach**: Scope to `_ingest_incremental()` with a fresh watermark (e.g. H1, latest `ts=09:30`, `now=10:00`, age < 1 bar) using a spy provider (from Bug Condition C5 in design)
  - Test that the function returns `0` and does NOT call the provider (`fetch_ohlcv` / `ingest_candles` not invoked) (assertion matches Property 6)
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexample (expected: a recorded `fetch_ohlcv` call when the watermark is fresh)
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.10, 2.10_

## Preservation Tests (write BEFORE any fix)

- [x] 7. Write preservation property tests (BEFORE implementing fixes)
  - **Property 7: Preservation** - Selftest Detection, Schedules, Ingestion, Calendar Runtime
  - **IMPORTANT**: Follow observation-first methodology
  - Observe behavior on UNFIXED code for non-bug inputs and record the actual outputs:
    - Selftest detection: `tests/unit/test_guardrail_selftest.py` green; a deliberately broken/monkeypatched gate still produces a problem entry; production path still rejects illegal candidates at construction via `model_validator`; `model_construct` never used in production (GI-5); GR-03 RR>=1.5, GR-04 SL in [0.5,3.0]xATR, GR-05 risk<=2% unchanged (Property 7 / Preservation Requirements)
    - Scan success: `scan_job` success resets `_fail_counts[key]=0` and sends no alert; `build_scan_schedules` emits exactly 4 entries for 2 instruments × 2 TFs; non-TwelveData seconds staggered (Property 8 / Preservation Requirements)
    - Ingestion: first-run backfill (`since = now - 120 days`, `limit = 500`, one provider call); stale-watermark incremental (`since = watermark - 2 bars`, `limit = 10`, one provider call); calendar parsing/normalization/429 handling functionally identical; non-crypto stale calendar fails CLOSE; system stays signal-only with `llm.enabled=false`, deterministic (Property 9 / Preservation Requirements)
  - **Property-based testing recommended** for `build_scan_schedules` (random instrument/TF combinations) and `_ingest_incremental` (random watermark ages) to generate many cases and catch edge cases
  - Write property-based / unit tests capturing the observed behavior patterns
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: Tests PASS (this confirms baseline behavior to preserve)
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

## Implementation

- [x] 8. Fix BUG 1 - selftest crash (`src/rtrade/guardrails/selftest.py`)

  - [x] 8.1 Implement the fix
    - Isolate illegal-candidate construction inside the selftest only: provide a selftest-local way to build known-bad candidates that bypasses the construction-time validators (e.g. `SignalCandidate.model_construct(...)` for the known-bad cases) so the object can reach `run_gate`
    - Keep the valid `good` candidate and the GR-10 mutation pair constructed via the real constructor so the regression check still proves a valid candidate passes
    - Ensure the bypass lives exclusively in `selftest.py` and is never imported or used on the production signal path (preserves GI-5)
    - _Bug_Condition: isBugCondition(SelftestRun) where constructing a known-bad SignalCandidate raises ValidationError that escapes run_guardrail_selftest()_
    - _Expected_Behavior: run_guardrail_selftest() returns list[str] without raising; run_worker continues on empty list, raises SystemExit(1) on non-empty (Property 1)_
    - _Preservation: Selftest still detects broken gates; production path still validates at construction; GI-5 holds; risk floors unchanged (Property 7)_
    - _Requirements: 2.1, 2.2_

  - [x] 8.2 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Selftest Returns Without Crashing
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - Run the bug condition exploration test from task 1
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.1, 2.2_

- [x] 9. Fix BUG 2 - alert regression (`src/rtrade/scheduler/jobs.py`)

  - [x] 9.1 Implement the fix
    - Reintroduce cooldown state: add module-level `_last_alert_at: dict[str, datetime] = {}` and a cooldown constant `_ALERT_COOLDOWN = timedelta(hours=2)`
    - Discriminate error type in the `except` block: catch / `isinstance(exc, RateLimitExceeded)` and skip the alert entirely for rate-limit errors while still incrementing `_fail_counts[key]`
    - Once-then-cooldown for other errors: for non-rate-limit errors at/above `_ALERT_THRESHOLD`, send the alert only if `key` has no recent `_last_alert_at` within the cooldown window; on send, record `_last_alert_at[key] = now`; include the error detail in the message
    - Preserve `_fail_counts[key] = 0` on success
    - _Bug_Condition: isBugCondition(ScanFailure) for RateLimitExceeded above threshold (alert sent), and non-RateLimitExceeded with missing _last_alert_at / re-send within cooldown_
    - _Expected_Behavior: rate-limit failures send no alert but still increment _fail_counts; non-rate-limit sends exactly one alert with detail then cooldown; _last_alert_at exists (Properties 2, 3)_
    - _Preservation: scan success resets _fail_counts[key]=0 and sends no alert (Property 8)_
    - _Requirements: 2.3, 2.4_

  - [x] 9.2 Verify bug condition exploration tests now pass
    - **Property 2: Expected Behavior** - Rate-Limit Alerts Suppressed
    - **Property 3: Expected Behavior** - Non-Rate-Limit Alert Once Then Cooldown
    - **IMPORTANT**: Re-run the SAME tests from tasks 2 and 3 - do NOT write new tests
    - Run the bug condition exploration tests from tasks 2 and 3
    - **EXPECTED OUTCOME**: Tests PASS (confirms bug is fixed)
    - _Requirements: 2.3, 2.4_

- [x] 10. Fix BUG 3 - scheduling burst (`src/rtrade/scheduler/main.py`)

  - [x] 10.1 Implement the fix
    - Spread TwelveData H1 across minutes: for TwelveData instruments assign per-instrument minutes from `["0","10","20","30"]` (by index) with `second="30"` instead of all-on-`"0"`
    - Move H4 off the H1 minute: use `minute="5"` with `hour="0,4,8,12,16,20"` for H4
    - Preserve non-TwelveData second-stagger logic and one entry per instrument×TF
    - _Bug_Condition: isBugCondition(ScheduleRequest) where provider is TwelveData and multiple H1 entries share minute="0" or H4 uses minute="0"_
    - _Expected_Behavior: H1 minutes ["0","10","20","30"] all second="30"; H4 minute="5" hour="0,4,8,12,16,20" (Property 4)_
    - _Preservation: 4 entries for 2 instruments × 2 TFs; non-TwelveData seconds staggered (Property 8)_
    - _Requirements: 2.5, 2.6_

  - [x] 10.2 Verify bug condition exploration test now passes
    - **Property 4: Expected Behavior** - TwelveData Schedules Spread Across Minutes
    - **IMPORTANT**: Re-run the SAME test from task 4 - do NOT write a new test
    - Run the bug condition exploration test from task 4
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.5, 2.6_

- [x] 11. Fix BUG 4 - mypy --strict (`src/rtrade/data/investing_calendar.py`, `src/rtrade/data/nasdaq_calendar.py`)

  - [x] 11.1 Implement the fix
    - Align `params` type: change the annotation from `dict[str, object]` to a type httpx accepts (e.g. `dict[str, str]`, or `httpx.QueryParams` / the `QueryParamTypes` alias); all current values are already strings so this is a pure annotation change with no runtime effect
    - Type the `_normalize_impact` argument: coerce/cast the looked-up impact value to `str | int` before passing it (e.g. wrap in `str(...)` or a typed local), removing the `Any | None` error without changing normalization output
    - _Bug_Condition: isBugCondition(MypyCheck) where a type error is reported on httpx .get(params=...) or on _normalize_impact(...)_
    - _Expected_Behavior: zero mypy --strict errors on those call sites; _normalize_impact argument is str | int (Property 5)_
    - _Preservation: calendar parsing/normalization/429 handling functionally identical; only types change (Property 9)_
    - _Requirements: 2.7, 2.8, 2.9_

  - [x] 11.2 Verify bug condition exploration test now passes
    - **Property 5: Expected Behavior** - Calendar Modules Type-Clean
    - **IMPORTANT**: Re-run the SAME check from task 5 - do NOT write a new test
    - Run `mypy --strict` on both calendar modules from task 5
    - **EXPECTED OUTCOME**: Check PASSES with zero type errors (confirms bug is fixed)
    - _Requirements: 2.7, 2.8, 2.9_

- [x] 12. Fix BUG 5 - wasteful incremental ingest (`src/rtrade/pipeline/scan.py`)

  - [x] 12.1 Implement the fix
    - Add a freshness short-circuit: after loading `latest`, if `latest is not None` and `now - ensure_utc(latest.ts) < timeframe_duration(tf)` (one bar), return `0` immediately without calling `ingest_candles`
    - Preserve the two existing branches: first-run (`latest is None`) backfill and stale-watermark incremental fetch unchanged
    - _Bug_Condition: isBugCondition(IngestRequest) where latest candle exists and age < 1 bar and the provider is still called_
    - _Expected_Behavior: return 0 and do not call the provider when watermark is fresh (Property 6)_
    - _Preservation: first-run backfill (since=now-120d, limit=500, one call); stale-watermark incremental (since=watermark-2 bars, limit=10, one call) (Property 9)_
    - _Requirements: 2.10_

  - [x] 12.2 Verify bug condition exploration test now passes
    - **Property 6: Expected Behavior** - Fresh Watermark Skips Provider
    - **IMPORTANT**: Re-run the SAME test from task 6 - do NOT write a new test
    - Run the bug condition exploration test from task 6
    - **EXPECTED OUTCOME**: Test PASSES (confirms bug is fixed)
    - _Requirements: 2.10_

- [x] 13. Verify preservation tests still pass
  - **Property 7: Preservation** - Selftest Detection, Schedules, Ingestion, Calendar Runtime
  - **IMPORTANT**: Re-run the SAME tests from task 7 - do NOT write new tests
  - Run the preservation property tests from task 7
  - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions across all five fixes)
  - Confirm all preservation tests still pass after the fixes (no regressions)
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_

## Checkpoint

- [x] 14. Checkpoint - Ensure all quality gates pass
  - Run `ruff check` - expect 0 issues
  - Run `ruff format --check` - expect 0 issues
  - Run `mypy --strict` - expect 0 errors
  - Run `pytest tests/unit` - expect 0 failed / 0 error
  - Confirm the worker starts without crashing (`run_worker()` completes selftest and proceeds to scheduler start on healthy code; fail-closes with `SystemExit(1)` when selftest reports a problem)
  - Ensure all tests pass; ask the user if questions arise
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11_
