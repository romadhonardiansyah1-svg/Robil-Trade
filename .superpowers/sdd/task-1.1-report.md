# Task 1.1 — Smart-exit realized P&L (A1) + pessimistic intrabar ordering (A2)

Branch: `fix/audit-remediation` · Python 3.12 · Windows/PowerShell · TDD (RED→GREEN)

## The two defects

### Defect A (finding A1): smart-exit realized P&L never applied
`ExitState.realized_r` (from partial TP) and `remaining_pct` were tracked but the
engine computed `r_multiple`/`pnl` on the FULL position from `fill_price → exit_price`
only. A trade that took a 50% partial at +1R then stopped at breakeven reported ~0R
instead of +0.5R, so every `smart_exits=True` backtest reported numbers that did not
match the modeled exits.

### Defect B (finding A2): intrabar look-ahead optimism
`apply_smart_exit` moved the stop to breakeven/trailing using the bar's FAVORABLE
extreme, then checked whether the bar's ADVERSE extreme hit the RAISED stop — i.e. it
assumed the high preceded the low. This let a bar that should have stopped out at the
original stop instead "escape" to a better breakeven/trailing exit. The non-smart path
(engine `else`-branch) was already correctly SL-first pessimistic.

## Call sites (both verified)
- `src/rtrade/backtest/engine.py` → the ONLY caller of `apply_smart_exit`. Fixed here.
- `src/rtrade/papertrack/virtual_exits.py` → does NOT call `apply_smart_exit`. It has
  its own parallel implementation (`_eval_partial_be`, `_eval_fixed`, `_check_hit`).
  The task brief assumed it shared `apply_smart_exit`; it does not. Its partial/BE
  policy already evaluates the stop on each bar before applying a new partial (the
  audit's note that live paper-track exits are pessimistic holds), so it was left
  untouched and its tests still pass. The `apply_smart_exit` signature was kept fully
  backward-compatible regardless (no signature change), so no second call site needed
  updating.

## Exact code changes

### `src/rtrade/backtest/smart_exit.py` (Defect B)
Reordered `apply_smart_exit` to the pessimistic model and updated the docstring:
1. Capture `stop_at_bar_start = state.current_sl` (the stop as it was at bar start).
2. Test the ADVERSE extreme against `stop_at_bar_start` FIRST. If hit → return `"SL"`
   immediately, applying NO partial / BE / trailing on this bar. This also preserves
   the `(sl_hit and tp_hit) → SL` worst-case rule because the SL check short-circuits
   before the TP check.
3. Only if the stop survived: compute `current_r` from the favorable extreme, apply
   partial TP / breakeven / trailing (these affect SUBSEQUENT bars), then check TP via
   the favorable extreme; if hit → return `"TP"`.

No change to the function signature or to `ExitState`/`SmartExitConfig` — backward
compatible for any caller.

### `src/rtrade/backtest/engine.py` (Defect A)
- Introduced realized-leg accounting locals before the exit phase:
  `realized_r = 0.0`, `remaining_pct = 1.0`, `partial_taken = False` (defaults describe
  a plain full-position trade, so the non-smart path is byte-identical).
- After the smart-exit per-bar loop, capture `realized_r`, `remaining_pct`,
  `partial_taken` from the final `exit_state`.
- Rewrote the PnL block to use total realized R:
  - `final_leg_r = (exit_price − fill_price)/sl_dist` for BUY,
    `(fill_price − exit_price)/sl_dist` for SELL (remaining fraction exits here).
  - `gross_r = realized_r + remaining_pct × final_leg_r`.
  - `net_r = gross_r − cost_r`; `trade.r_multiple = net_r`;
    `trade.pnl = net_r × risk_amount` where `risk_amount = equity × risk_pct/100`
    (full position risking `sl_dist` == `risk_amount`, so PnL scales with R).
  - For the non-smart path (`realized_r=0, remaining_pct=1, partial_taken=False`) this
    reduces to the previous `(raw_pnl − cost) × position_size`, i.e. unchanged.

## How costs interact with partials
Costs are charged conservatively (pessimistically, never under-counted):
- A full round-turn is charged on the whole position: `cost_r = trade.cost / sl_dist`
  (in R terms), identical to the prior model.
- When a partial is taken, the extra exit-side crossing on the closed fraction is
  charged additionally: `cost_r += (trade.cost / 2.0) × closed_fraction / sl_dist`,
  where `closed_fraction = 1 − remaining_pct`. `compute_trade_cost` returns a round-turn
  cost, so half of it approximates one side. This guarantees a partialled trade is never
  cheaper than the equivalent single-exit trade.
- `cost_model=None` (no costs) leaves the realized R exact, which is what the RED P&L
  test relies on.

## RED tests (with bar sequences and expected R)

### RED 1 — engine P&L (`tests/backtest/test_engine_smart_exit_pnl.py::test_partial_then_breakeven_reports_half_r`)
BUY, entry_limit=100, SL=95 (sl_dist=5), TP=110, valid_bars=3. UTC-indexed bars
(open, high, low, close):
- bar 0: (100,100,100,100) signal bar
- bar 1: (100,101,99,100) → fills at 100 (`fill_bar=1`)
- bar 2: (100,105,100.5,104) → +1R: partial 50% (realized 0.5R), BE→100
- bar 3: (100,100,99,100) → low 99 ≤ BE 100 → stop at 100

Expected: `exit_reason="SL"`, `exit_price=100`, `r_multiple ≈ +0.5`
(= 0.5 realized + 0.5 × 0R at breakeven). A guard test
`test_full_position_tp_unchanged` asserts a clean +2R TP (no partial) still reports
`r_multiple ≈ 2.0`.

RED evidence: failed with `r_multiple == 0` (full-position fill→exit gave 100−100=0).

### RED 2 — intrabar pessimism (`tests/unit/test_smart_exit.py::TestIntrabarPessimism`)
Single post-fill bar.
- BUY: entry=100, original_sl=95, TP=110, bar_high=106 (+1.2R favorable, would take a
  partial and move BE to 100), bar_low=94 (below the ORIGINAL stop 95).
  Expected: `reason="SL"`, `current_sl==95` (NOT raised to 100), `partial_taken=False`,
  `be_moved=False`, `realized_r==0`, `remaining_pct==1`.
- SELL mirror: entry=100, original_sl=105, TP=90, bar_low=94 (favorable), bar_high=106
  (above original stop 105). Expected `reason="SL"`, `current_sl==105`, no partial/BE.

RED evidence: failed with `current_sl == 100.0` (BUY) / `100.0` (SELL) — the stop had
been optimistically raised to breakeven before the adverse extreme was tested.

## Commands and output

RED (before fix):
```
.venv\Scripts\pytest.exe -q tests/backtest/test_engine_smart_exit_pnl.py tests/unit/test_smart_exit.py
FAILED tests/backtest/test_engine_smart_exit_pnl.py::test_partial_then_breakeven_reports_half_r
FAILED tests/unit/test_smart_exit.py::TestIntrabarPessimism::test_adverse_extreme_stops_before_partial_and_be
  assert 100.0 == 95.0
FAILED tests/unit/test_smart_exit.py::TestIntrabarPessimism::test_sell_adverse_extreme_stops_before_partial_and_be
  assert 100.0 == 105.0
```

GREEN (after fix):
```
.venv\Scripts\pytest.exe -q tests/backtest/test_engine_smart_exit_pnl.py tests/unit/test_smart_exit.py
.......... (10 passed)
```

Full backtest dir + smart-exit:
```
.venv\Scripts\pytest.exe -q tests/backtest tests/unit/test_smart_exit.py  → all passed
```

Paper-tracker (second caller) + analytics:
```
.venv\Scripts\pytest.exe -q tests/unit/test_virtual_exits.py tests/unit/test_analytics.py  → 7 passed
```

Full suite:
```
.venv\Scripts\pytest.exe -q  → all passed (ssssss = skips; only an unrelated
starlette/httpx deprecation warning from FastAPI testclient)
```

Lint / types:
```
.venv\Scripts\ruff.exe check src tests   → All checks passed!
.venv\Scripts\mypy.exe src               → Success: no issues found in 129 source files
```

## Commit
`fix(backtest): apply smart-exit realized P&L + pessimistic intrabar ordering (A1,A2)`
Hash: 94f3f0104d67e8f26dab30a49b336b0b45b8b10c

## Concerns
- Brief premise vs reality: the paper-tracker does NOT share `apply_smart_exit`; it
  duplicates the partial/BE logic in `virtual_exits.py`. That duplication is a latent
  DRY risk — the two implementations can drift. Out of scope here (no behavior change
  needed; its tests pass), but worth a follow-up to consolidate onto `apply_smart_exit`.
- Existing smart-exit unit tests live at `tests/unit/test_smart_exit.py`, not
  `tests/backtest/test_smart_exit.py` as the brief stated; I extended the real file and
  added the new engine P&L test under `tests/backtest/` per the audit plan.
- Partial-cost surcharge (extra half round-turn on the closed fraction) is a modeling
  choice on the conservative side; if a more precise per-fill cost model is desired it
  should be driven from `CostModel` rather than this approximation.
- The pessimistic model can never re-check a raised stop on the same bar by design;
  this is the intended A2 behavior (BE/trailing affect subsequent bars only).
