# Task 1.8 — Backtest realism: timeframe-aware warmup + gap-aware SL/TP fills (A8, A9)

Branch: `fix/audit-remediation`
Scope: `src/rtrade/backtest/harness.py`, `src/rtrade/backtest/engine.py`
Method: strict TDD (RED → GREEN), one commit.

## Defects fixed

### A8 — Walk-forward warmup sized in HOURS regardless of timeframe
`run_walkforward_harness` (harness.py) reserved warmup with
`warmup_start = train_end_ts - pd.Timedelta(hours=warmup_bars)`, i.e. it assumed
1 bar == 1 hour. On D1 this reserves only ~10 days for `warmup_bars=250`
(EMA200 never warms → OOS contamination); on M5 it reserves far too much.

**Bar-duration inference approach.** The real bar size is inferred once, before
the window loop, from the median spacing of the data index:

```python
bar_dur = pd.Series(df.index).diff().median() if len(df.index) >= 2 else pd.NaT
```

`median()` is robust to occasional gaps / weekend holes and works for any
timeframe. Per window the warmup origin becomes:

```python
if pd.isna(bar_dur):
    warmup_start = train_end_ts - pd.Timedelta(hours=warmup_bars)   # guard / fallback
else:
    warmup_start = train_end_ts - warmup_bars * bar_dur
```

Guard: fewer than 2 rows (or a NaT median) falls back to the legacy hourly
behavior rather than crashing.

**Latent bug exposed by the fix.** Once a daily window is actually *processed*
(the buggy hours path always skipped it via `len(wf_df) < warmup_bars + 10`), the
next line `first_test_iloc = int(test_mask.values.argmax())` raised
`AttributeError: 'numpy.ndarray' object has no attribute 'values'`, because
`wf_df.index >= test_start_ts` already returns an ndarray. Changed to
`int(test_mask.argmax())`. This is required for A8 to function and was previously
unreachable in tests because every window was being skipped.

### A8 — walkforward.py check
`src/rtrade/backtest/walkforward.py` (`run_walk_forward`) was inspected: it sizes
windows by **months** (`generate_windows` / `_add_months`) and has **no**
`Timedelta(hours=...)` warmup assumption. Per the task instruction ("if it sizes
windows by months, leave it") it was left unchanged.

### A9 — SL/TP filled at the exact level even when the bar GAPS through it
`engine.py`. The dead line `df["open"].astype(float).values` (computed opens but
discarded them) is now assigned: `opens = df["open"].astype(float).values`.

Pessimistic, asymmetric gap-fill model on a bar that OPENS beyond the level:

- **Stop loss = stop order → slips with the gap.** Fill at the WORSE of stop and
  open. BUY: `exit_price = min(stop, open)`; SELL: `exit_price = max(stop, open)`.
- **Take profit = limit order → never improves on a gap.** Fill at exactly the TP
  level (no improvement to the gapped open).

`min`/`max` is self-guarding: when the open is *not* beyond the level the result
equals the level, so non-gap fills are unchanged. Applied to BOTH paths:

- Non-smart else-branch: the `(sl_hit and tp_hit) or sl_hit` branch now uses
  `min/max(stop, open)`; the `tp_hit` branch keeps `take_profit`.
- Smart-exit path: the `exit_reason == "SL"` branch applies the same
  `min/max(exit_state.current_sl, open)`; the TP branch keeps `take_profit`.

The existing `(sl_hit and tp_hit) → SL` worst-case rule and the smart-exit
pessimistic intrabar ordering (A1/A2) are untouched.

## Exact code changes

`engine.py`
- `df["open"].astype(float).values` → `opens = df["open"].astype(float).values`.
- Smart-exit SL branch: `min/max(exit_state.current_sl, opens[i])` by direction.
- Non-smart SL branch: `min/max(trade.stop_loss, opens[i])` by direction;
  TP branch comment clarifies "limit order: no gap improvement".

`harness.py`
- Inferred `bar_dur` once before the window loop.
- `warmup_start` now `train_end_ts - warmup_bars * bar_dur` (hourly fallback when
  `bar_dur` is NaT).
- `test_mask.values.argmax()` → `test_mask.argmax()` (ndarray fix).

## RED evidence (before GREEN)
```
FAILED tests/backtest/test_engine_gap_fills.py::TestNonSmartGapFills::test_buy_sl_gap_down_fills_at_open
FAILED tests/backtest/test_engine_gap_fills.py::TestNonSmartGapFills::test_sell_sl_gap_up_fills_at_open
FAILED tests/backtest/test_engine_gap_fills.py::TestSmartGapFills::test_buy_smart_sl_gap_down_fills_at_open
FAILED tests/unit/test_harness.py::TestWalkForwardWarmupTimeframeAware::test_daily_warmup_reserves_days_not_hours
```
Representative failure: smart SL gap-down → `assert 95.0 == 90.0` (filled at the
stop level instead of the gapped open). A8 → `assert 0 >= 1` (every daily window
skipped, `per_window_metrics == []`). The TP-gap guard test passed before and
after (TP correctly never improved), as expected.

## GREEN evidence
- Targeted: `tests/backtest/test_engine_gap_fills.py` +
  `tests/unit/test_harness.py::TestWalkForwardWarmupTimeframeAware` → 5 passed.
- Suites: `tests/backtest tests/unit/test_backtest.py tests/unit/test_harness.py`
  → all passed.
- Full suite: `810 passed, 7 skipped` in ~88s.
- `ruff check src tests` → All checks passed.
- `mypy src` (strict) → Success: no issues found in 129 source files.

## New tests
- `tests/unit/test_harness.py`: `TestWalkForwardWarmupTimeframeAware` +
  `_make_daily_trend_df` (500 daily bars; one 12mo/3mo window must be processed —
  hours-based warmup wrongly skips it).
- `tests/backtest/test_engine_gap_fills.py`: BUY SL gap-down, SELL SL gap-up,
  BUY TP gap-up (no improvement, guard), BUY smart SL gap-down.

## Existing tests changed
None changed. `test_engine_smart_exit_pnl.py::test_partial_then_breakeven_reports_half_r`
was a regression risk (BE stop bar opens at 100 == BE level → `min(100, 100) == 100`,
unchanged), and it still reports +0.5R. No existing assertions were modified.

## Concerns
- The `test_mask.values` ndarray bug means the daily/non-hourly walk-forward path
  was effectively never exercised before this change; recommend a follow-up that
  runs the harness on M5/M15/H4 data to widen coverage of the warmup math.
- `bar_dur` uses the median spacing; on extremely gappy/illiquid series the median
  is still the right central estimate, but a dataset with <2 rows silently falls
  back to hours — acceptable here since such inputs cannot form a window anyway.
