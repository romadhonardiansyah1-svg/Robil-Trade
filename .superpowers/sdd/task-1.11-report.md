# Task 1.11 — DEFECT A10: Chronological equity curve for overlapping trades

**File:** `src/rtrade/backtest/engine.py` (`run_backtest`)
**Tests:** `tests/backtest/test_engine_chronological.py` (new)
**Branch:** `fix/audit-remediation`
**Status:** DONE — RED→GREEN, full suite + ruff + mypy clean, single commit.

## The distortion

`run_backtest` iterated trades in **signal order** and, inside that loop, did
`equity += trade.pnl` and `equity_curve.append(equity)` once per trade. The
equity curve was therefore ordered by *when a signal was processed*, not by
*when capital was actually won or lost*.

For a single position (or any non-overlapping sequence) signal order already
equals exit-time order, so this was invisible. But when trades **overlap in
time** the booked order diverges from chronology, which warps the curve shape
and every curve-derived metric:

- `max_drawdown_pct` (computed in `metrics.compute_metrics` from the running
  peak of `equity_curve`) is measured against the wrong peaks/troughs.
- `total_return` end-points are unaffected (PnL sums commute), but the *path*
  between them is wrong.
- Sharpe is derived from per-trade R-multiples, which are order-independent, so
  Sharpe is **not** directly distorted — but anything reading the curve shape is.

Concrete example (the RED test): a +20R winner (A) opens first and exits LAST,
while a −1R loser (B) opens later and exits EARLIER.
- Signal order books A first → peak 12,000 → the −120 loss looks like a **1.0%**
  blip off an inflated peak.
- Chronologically the −120 loss is realized FIRST (off the 10,000 starting
  capital, a **1.2%** drawdown), and the winner only closes afterwards.
The real risk experienced (1.2% DD) is hidden by signal-order booking (1.0% DD).

## The fix — chronological equity curve

After every trade's fill / exit / PnL / R-multiple is computed (logic
unchanged), the curve is rebuilt by accumulating realized PnL of **filled**
trades sorted by `(exit_bar, fill_bar)`:

```python
curve_equity = initial_equity
equity_curve = [curve_equity]
for trade in sorted(
    (t for t in trades if t.fill_price is not None and t.pnl is not None),
    key=_curve_order_key,            # (exit_bar, fill_bar)
):
    curve_equity += trade.pnl or 0.0
    equity_curve.append(curve_equity)
```

`exit_bar` is the primary key (chronology of realizations); `fill_bar` is a
stable tie-break for trades closing on the same bar. The per-trade
`equity_curve.append` inside the processing loop was removed.

### Risk-sizing basis kept (documented)

Risk sizing is **unchanged**: each trade still risks `risk_pct` of the *running
equity in signal order* (`risk_amount = equity * (risk_pct/100)`, with
`equity += trade.pnl` accumulated in signal order purely as the sizing basis).
This was a deliberate choice:

- It keeps the single-position / non-overlapping case **byte-for-byte
  identical** (sizing values, PnL, R-multiples and curve all unchanged).
- PnL is order-independent for the *sum*, so `final_equity` is identical whether
  accumulated in signal or exit order — only the curve *path* (and thus
  drawdown) changes, which is exactly the A10 correctness target.

Changing the sizing basis as well would have silently moved results for the
non-overlap case, which the task forbids. The A10 fix is scoped to the
curve-accumulation order only.

### Trade log order

`BacktestResult.trades` is **left in its existing signal order** (not reordered).
Only the equity-curve accumulation order changed. No consumers needed updating:
`harness.py` / `walkforward.py` read `r_multiples` from `trades` (order-independent)
and consume `equity_curve` only via `compute_metrics`, which treats it as an
ordered series — now correctly chronological.

## Proof the non-overlap case is unchanged

- A10 only reorders curve accumulation by `(exit_bar, fill_bar)`. When trades do
  not overlap, each trade's `exit_bar` precedes the next trade's `fill_bar`, so
  exit-time order == signal order and the curve is produced in the same order
  as the old per-trade append.
- `final_equity` is the signal-order running `equity` (unchanged).
- Guard test `test_non_overlapping_trades_match_sequential_accumulation`: A exits
  at bar 2 before B fills at bar 5; asserts the curve is exactly the sequential
  accumulation `[10000.0, 10100.0, 10201.0]`. Passes.
- All pre-existing backtest tests (single-signal) pass untouched.

## RED overlapping-trades test

`tests/backtest/test_engine_chronological.py::test_overlapping_trades_curve_is_exit_time_ordered`
constructs two overlapping BUY trades on hand-built tz-aware UTC OHLC bars:
- Trade A: `bar_index=0`, fills bar 1 @100, SL 90, TP 300 → exits **LAST** (bar 8, TP, +20R).
- Trade B: `bar_index=2`, fills bar 3 @100, SL 99 → exits **EARLIER** (bar 5, SL, −1R).

PnL: A = 20R × (10000×1%) = **+2000**; B = −1R × (12000×1%) = **−120**.

Asserts:
- curve == `[10000.0, 9880.0, 11880.0]` (exit-time order: B then A);
- curve != `[10000.0, 12000.0, 11880.0]` (the old signal-order curve);
- `final_equity == 11880.0`;
- chronological `max_drawdown_pct` (1.2%) **>** signal-order `max_drawdown_pct`
  (1.0%) — the loss only surfaces as the true drawdown under correct ordering;
- trade log stays in signal order (`trades[0].bar_index==0`, `trades[1].bar_index==2`).

### RED (before fix)

```
assert result.equity_curve == [10_000.0, 9_880.0, 11_880.0]
E   assert [10000.0, 12000.0, 11880.0] == [10000.0, 9880.0, 11880.0]
E     At index 1 diff: 12000.0 != 9880.0
FAILED ...test_overlapping_trades_curve_is_exit_time_ordered
```
(The non-overlap guard test already passed, and every fill/exit/R-multiple
sanity assertion in the RED test passed — confirming the failure is purely the
signal-vs-chronological ordering, not engineered-data error.)

### GREEN (after fix)

```
tests/backtest/test_engine_chronological.py ..                            [100%]  (2 passed)
```

## Existing tests changed

**None.** No existing test involved overlapping trades, so no existing expected
value legitimately changed. Only the new file was added.

## Verification

| Check | Command | Result |
|-------|---------|--------|
| RED | `pytest -q tests/backtest/test_engine_chronological.py` | 1 failed, 1 passed (as designed) |
| GREEN | `pytest -q tests/backtest/test_engine_chronological.py` | 2 passed |
| Targeted | `pytest -q tests/backtest tests/unit/test_backtest.py` | 54 passed |
| Full suite | `pytest -q` | all passed, 7 skipped (1 pre-existing unrelated Starlette deprecation warning) |
| Lint | `ruff check src tests` | All checks passed |
| Types | `mypy src` (strict) | Success: no issues found in 129 source files |

## A1/A2/A9 — not regressed

Fill, smart-exit realized-leg P&L (A1), pessimistic intrabar SL-first (A2) and
gap-fill exit pricing (A9) logic is entirely untouched; only the post-loop curve
assembly changed. Full suite (incl. `test_engine_gap_fills.py`,
`test_engine_smart_exit_pnl.py`) green.

## Commit

`fix(backtest): chronological equity curve for overlapping trades (A10)`
Single commit on `fix/audit-remediation` (hash recorded in `git log`).

## Concerns

- **Sizing vs. curve consistency under overlap.** Sizing remains signal-order
  (documented above) while the curve is chronological. Under heavy overlap the
  *sizing* basis still uses signal-order running equity, which is a separate
  (smaller) modeling choice than the A10 curve fix; revisiting it would change
  non-overlap results and was explicitly out of scope. Sizing each overlapping
  trade off equity-at-entry-time would be the next refinement if desired.
- Trades still open at data end (`exit_reason == "OPEN"`) are booked at their
  forced `exit_bar = n_bars-1`, so they sort to the end of the curve — correct
  for chronology, but their mark-to-market exit remains an approximation.
- `compute_metrics` `total_return` uses only curve end-points, so it is unchanged;
  only `max_drawdown_pct` (and any future curve-shape metric) benefits from the fix.
