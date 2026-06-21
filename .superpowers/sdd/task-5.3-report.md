# Task 5.3 — Signal Correctness Defects (F3, F4, F5)

Branch: `fix/audit-remediation`. TDD (RED → GREEN → full suite + lint/type).

## F3 — `round_to_tick` under-rounds non-decade tick sizes
File: `src/rtrade/signals/levels.py` (`round_to_tick` / `_decimals`).

Root cause: the multiple computation `round(price / pip_size) * pip_size` was
already correct, but `_decimals` derived the place count via
`-floor(log10(pip_size))`. For a tick like `0.25` that yields `1`, so the final
`round(result, 1)` knocked the value back off the grid (e.g. `100.25 → 100.2`).

Fix: derive the decimal count from the tick's own decimal representation using
`Decimal(str(pip_size)).normalize().as_tuple().exponent`. This gives `0.25 → 2`,
`0.5 → 1`, `0.01 → 2`, `0.0001 → 4`, `5 → 0`, etc. Power-of-ten ticks are
unchanged because their decimal representation already matches the old log10
result. The multiple-of-tick rounding (`round(price/tick)*tick`) was retained.

Verified on grid: `100.26/0.25→100.25`, `100.30/0.25→100.25`, `100.40/0.25→100.50`,
`0.5` and `5` grids, and preserved power-of-ten (`1.23456/0.0001→1.2346`,
`2705.123/0.01→2705.12`).

## F4 — volume SMA included the trigger bar (low bias)
File: `src/rtrade/signals/confluence.py` (`score_volume`).

Root cause: `vol.rolling(20, min_periods=1).mean()` and taking `.iloc[-1]`
includes the current/trigger bar in the SMA20 baseline, biasing the volume ratio
low. This was inconsistent with `edge_quality._volume_ratio`, which excludes the
current bar.

Fix: baseline = mean of the prior 20 bars EXCLUDING the current bar
(`vol.iloc[-(window+1):-1].mean()`), ratio = `current / baseline`, with guards
for `< 2` bars and `baseline <= 0`.

### edge_quality consistency check
`edge_quality._volume_ratio` computes `lookback = vol.tail(max(2, window))`,
`baseline = median(lookback.iloc[:-1])`, `latest = lookback.iloc[-1]` — i.e. it
EXCLUDES the current bar. `score_volume` now matches that exclusion convention
(it uses the mean for an SMA rather than the median, but both exclude the trigger
bar). Test `test_consistent_with_edge_quality_exclusion` constructs a series with
constant prior bars (where mean == median) and asserts both produce ratio `1.5`,
demonstrating the two are now consistent.

## F5 — "nearest level" picked first-by-price, not nearest-to-entry
File: `src/rtrade/signals/confluence.py` (`score_structure`).

Root cause: the loop iterated `sr_levels` (sorted ascending by price) and
`break`-ed at the first level within tolerance — that is first-by-price, not
nearest-to-entry.

Fix: filter to levels within tolerance, then
`min(in_tolerance, key=lambda lvl: abs(lvl.price - entry))` selects the true
nearest. Scoring/threshold logic is otherwise unchanged.

## Existing tests changed
None. Existing `TestRoundToTick` cases (power-of-ten / integer ticks) still pass
unchanged. No existing test asserted the old biased volume value or the
first-by-price level behavior, so nothing required updating. New tests were added:
- `tests/unit/test_signals.py::TestRoundToTick` — quarter/half/five/power-of-ten grid cases.
- `tests/unit/test_confluence_scoring.py` — F4 exclusion + edge_quality consistency, F5 nearest-by-distance.

## Verification
- RED: `test_quarter_tick_lands_on_grid` (100.2 ≠ 100.25), `test_baseline_excludes_trigger_bar` (10 ≠ 15), `test_picks_nearest_level_not_first_by_price` (7 ≠ 20) — all failed as expected.
- GREEN: targeted tests pass.
- Full suite: `.venv\Scripts\pytest.exe -q` — all pass (7 skipped), exit 0.
- `.venv\Scripts\ruff.exe check src tests` — All checks passed.
- `.venv\Scripts\mypy.exe src` — Success: no issues found in 129 source files.

## Concerns
- F4 uses mean (SMA, per task + existing bucket semantics) while edge_quality
  uses median; consistency is on the current-bar EXCLUSION, not the central
  statistic. If full numeric parity is later required, both should use the same
  statistic.
- `score_volume` window is hard-coded at 20 (matches the original); it is not
  wired to `edge_quality`'s configurable `volume_window`.
