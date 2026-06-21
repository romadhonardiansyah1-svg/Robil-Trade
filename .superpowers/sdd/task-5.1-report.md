# Task 5.1 — Indicator Engine Remediation (F1, F2)

File: `src/rtrade/indicators/engine.py` · Tests: `tests/unit/test_indicators.py`
Branch: `fix/audit-remediation` · Method: TDD (RED → GREEN), one commit.

## F2 — compute() mutated the caller's DataFrame (fixed first, prerequisite)

`compute()` performed in-place float coercion on OHLCV columns and assigned ~16
indicator columns onto the **input** frame, violating the documented "pure, no
side effects" contract. Callers only avoided damage by reassigning the return value.

Fix: added `df = df.copy(deep=True)` immediately after the input assertions, before
any dtype coercion or column assignment. All subsequent work happens on the copy,
and that copy is returned. Verified no internal code path relies on mutating the
original (the engine only reads OHLCV and writes indicator columns; `snapshot()`
reads from the returned frame).

## F1 — VWAP daily anchoring

Previous implementation computed a whole-frame cumulative
`(typical·volume).cumsum() / volume.cumsum()` over the entire DataFrame (e.g. 500
bars), so the value fed to `IndicatorSnapshot` / the LLM context pack was not a
daily mean — it drifted across many days with no reset.

Fix: group by the **UTC calendar day** of each bar's index timestamp and take the
cumulative typical-price·volume sum divided by the cumulative volume **within each
day**, so VWAP restarts at the first bar of every UTC day. Typical price formula is
unchanged: `(high + low + close) / 3`.

Day key derivation (robust to both index forms):
- tz-aware index → `tz_convert("UTC").normalize()`
- tz-naive index → `normalize()` (treated as already-UTC per the engine contract;
  this preserves the existing `test_harness` fixtures which use tz-naive indexes)
- A non-DatetimeIndex triggers an assertion (VWAP requires a datetime index).

### Zero-volume handling (documented choice)
- Zero-volume **bars** within a day contribute 0 to both numerator and denominator,
  so VWAP simply carries the prior intraday value — sensible for an intraday mean.
- A fully zero-volume **day** yields `NaN` via `cumvol.replace(0, np.nan)` rather
  than dividing by zero. NaN (not carry-forward) is chosen so a day with no traded
  volume is explicitly "no value" instead of silently inheriting the previous day's
  mean — which would defeat the daily-anchor intent. `snapshot()` already maps a NaN
  vwap to `None`.

## Tests added (RED first, confirmed failing)

- `TestComputePurity::test_compute_does_not_mutate_input` — snapshots the input's
  columns, dtypes, index and values, calls `compute(df)`, asserts the original frame
  is byte-for-byte unchanged and the returned frame is a distinct object that did get
  indicators. RED: "compute() added columns to input" (16 extra columns).
- `TestVwapDailyAnchor::test_vwap_resets_on_first_bar_of_new_utc_day` — 2 UTC days of
  hourly bars; asserts day-2 open VWAP == that bar's typical price (proves reset).
  RED: got 150.0, expected 200.0 (no reset — carried day-1 accumulation).
- `TestVwapDailyAnchor::test_vwap_differs_from_non_reset_cumulative` — asserts the
  daily-anchored last-bar VWAP differs from the whole-frame cumulative and equals the
  day-2-only cumulative. RED: equalled the non-reset cumulative.

No existing test asserted a specific engine VWAP numeric value, so **no existing test
required updating** for the corrected value. (`s3_mtf_scalper` computes its own
separate `s3_vwap` rolling-window column and is unaffected; `test_context_pack_fencing`
constructs an `IndicatorSnapshot` literal.)

## Verification

- RED: 3 new tests failed for the expected reasons (above).
- GREEN: `tests/unit/test_indicators.py` — all pass.
- Consumers: `test_s3_mtf_scalper.py`, `test_context_pack_fencing.py` — pass.
- Regression caught & fixed: `test_harness.py` uses tz-naive indexes; initial
  `tz_convert` raised TypeError. Added the tz-naive branch (treat as UTC) — all pass.
- FULL suite `.venv\Scripts\pytest.exe -q`: all pass, 7 skipped, 0 failures.
- `.venv\Scripts\ruff.exe check src tests`: All checks passed.
- `.venv\Scripts\mypy.exe src` (strict): Success, no issues (129 files).

## Concerns / follow-ups

- VWAP daily anchoring uses the UTC calendar day. If a session-based anchor (e.g.
  17:00 ET futures session) is ever desired, the day key would need a session offset;
  out of scope here.
- tz-naive indexes are silently treated as UTC to preserve existing fixtures. Long
  term, enforcing tz-aware UTC indexes at the engine boundary would be cleaner and
  match the GLOBAL tz-aware UTC constraint.
