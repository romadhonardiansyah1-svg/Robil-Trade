# Task 6.1 — Structure & Data defect remediation (F6, F7, D4)

Branch: `fix/audit-remediation`. One commit. TDD (RED → GREEN). tz-aware UTC throughout,
no `print()`. `ruff check src tests` and `mypy src` (strict) both clean.

## F6 — Order/path-independent S/R clustering
**File:** `src/rtrade/indicators/structure.py` → `cluster_sr_levels`

- **Before:** each price-sorted point was compared to the cluster's CURRENT running
  mean (`abs(point.price - cluster_mean) <= tolerance`). The mean DRIFTS as points are
  added, so the cluster width and the resulting levels depended on the accumulation path
  and could grow without a single-linkage bound.
- **After:** standard single-linkage on the price-sorted points — a NEW cluster starts
  whenever `point.price - prev_point.price > tolerance` (gap to the PREVIOUS point, not
  the drifting mean). Deterministic and order-independent (shuffled input → identical
  levels) given the existing price sort. `min_touches`, the avg-price level value, and the
  resistance/support majority logic are unchanged.
- **Tie-break (documented):** on an exact tie (equal highs and lows touching a level) the
  level is now classified as **resistance** via `is_resistance = highs_count >= lows_count`.
  This is an explicit, documented choice replacing the old implicit
  `highs_count > len(cluster)/2`, which silently defaulted ties to support.

## F7 — Equal-high/low (double tops/bottoms) now detected
**File:** `src/rtrade/indicators/structure.py` → `detect_swing_points`

- **Before:** `np.sum(window_h == highs[i]) == 1` disqualified a swing high whenever an
  EQUAL high existed anywhere in the same window — dropping the equal-high/low liquidity
  pools SMC relies on (e.g. two equal highs inside one window produced ZERO swings).
- **After (documented rule):** a swing high at bar `i` when ALL hold:
  1. `highs[i] == max(window)`,
  2. `i` is the LEFTMOST bar in the window holding that max (`np.argmax(window) == left`,
     since argmax returns the first occurrence), and
  3. `highs[i]` strictly exceeds at least one adjacent bar
     (`highs[i] > highs[i-1] or highs[i] > highs[i+1]`) — excludes a perfectly flat interior.
  Symmetric for swing lows (`np.argmin`, strictly-less-than adjacent). A flat top of N
  equal bars therefore yields EXACTLY ONE swing high (its leftmost bar), not one per bar,
  while genuine double tops/bottoms are detected. Fully deterministic.

## D4 — Timeframe-aware candle-gap heuristic
**File:** `src/rtrade/data/ingestion.py` → `detect_candle_gaps`

- **Before:** weekend/holiday suppression used `missing_count <= 72`, assuming 1 bar == 1
  hour (H1). On D1 this hid real multi-week gaps; on M5/M15 it spammed false gaps on normal
  weekends.
- **After:** suppression threshold is derived from the timeframe duration. A documented
  weekend span `_WEEKEND_SPAN = timedelta(hours=72)` (Fri close → Sun/Mon open, with a small
  holiday cushion) yields `weekend_bars = ceil(_WEEKEND_SPAN / timeframe_duration(tf))`.
  A non-crypto gap that starts on/after Friday and spans `<= weekend_bars` is suppressed;
  larger gaps are flagged. Per timeframe: D1→3 bars, H1→72 bars (regression-preserving),
  M5→864 bars.
- **Callers:** `detect_candle_gaps` already had a `timeframe: Timeframe` parameter and the
  sole caller `ingest_candles` already passed it positionally
  (`detect_candle_gaps(candles, timeframe, is_crypto=is_crypto)`). No new param threading
  was required; grep of callers confirmed `src/rtrade/data/ingestion.py:151` is the only
  call site.

## Existing tests changed
None. The pre-existing `test_sr_clustering`, `test_swing_points_detected`, and
`test_gap_detection` still pass unchanged under the new logic (their assertions remained
valid). No prior test had its expected values modified.

## Tests added
- `tests/unit/test_indicators.py` (class `TestStructure`):
  - F6: `test_sr_clustering_single_linkage_chain`, `test_sr_clustering_order_independent`,
    `test_sr_clustering_split_on_gap`, `test_sr_clustering_tie_break_is_resistance`.
  - F7: `test_swing_detects_double_top_in_window`, `test_swing_flat_top_yields_single_high`,
    `test_swing_detects_double_bottom_in_window`.
- `tests/unit/test_detect_candle_gaps.py` (new file):
  - D4: `test_d1_multiweek_gap_is_flagged`, `test_m5_normal_weekend_not_overflagged`,
    `test_h1_weekend_still_suppressed`, `test_h1_midweek_gap_still_flagged`,
    `test_crypto_gap_always_flagged`.

## RED → GREEN evidence
- RED (before implementation): 6 targeted tests failed exactly as designed —
  `test_sr_clustering_single_linkage_chain` (drifting mean split the chain),
  `test_sr_clustering_tie_break_is_resistance` (tie defaulted to support),
  `test_swing_detects_double_top_in_window` / `..._double_bottom_in_window` (equal extremes
  dropped → 0 swings), `test_swing_flat_top_yields_single_high` (0 swings), and
  `test_d1_multiweek_gap_is_flagged` (10-day Friday gap suppressed by `<= 72`).
- GREEN: targeted file run → all pass.
- Full suite: **904 passed, 8 skipped, 1 warning** (pre-existing Starlette/httpx deprecation,
  unrelated).
- `ruff check src tests` → "All checks passed!".
- `mypy src` (strict) → "Success: no issues found in 129 source files".

## Commit
`fix(structure/data): order-independent S/R clustering + equal-extreme swings + tf-aware gap heuristic (F6,F7,D4)`
Hash: `6d1416bd8de8cc205f42838860917433504261b1`

## Concerns
- F7 "leftmost in window" rule: when two equal highs fall inside the SAME fractal window,
  only the LEFT one is marked (by design, to avoid spam). Double tops whose peaks are
  separated by more than `right` bars still produce two swings; very close equal highs
  produce one. This satisfies "detect at least one at that price" without duplicate spam.
- D4 weekend span is a fixed 72h heuristic; markets with longer holiday closures (e.g. a
  multi-day exchange holiday on D1) may still be flagged. That is intentional — better to
  surface a real gap than hide it. Tune `_WEEKEND_SPAN` if a specific holiday calendar is
  introduced.
