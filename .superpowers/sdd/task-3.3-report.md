# Task 3.3 — Scheduler jobs: D3 + E2 remediation

Branch: `fix/audit-remediation`
File changed: `src/rtrade/scheduler/jobs.py`, `src/rtrade/persistence/audit_chain.py`
Tests: `tests/unit/test_scheduler_jobs.py`, `tests/unit/test_audit_chain.py`

## Summary

Both defects in `src/rtrade/scheduler/jobs.py` are fixed under strict TDD (RED → GREEN),
with ruff + mypy(strict) clean and the full suite passing.

---

## DEFECT D3 — `audit_chain_verify_job` checked the FIRST 1000 rows, not the latest

### Root cause
The job issued `select(SignalAudit).order_by(SignalAudit.id.asc()).limit(1000)` despite the
docstring claiming "Samples last 1000". Once `signal_audits` exceeds 1000 rows, the window is
permanently pinned to the oldest rows (id 1..1000) and **recent rows are never integrity-checked** —
exactly the rows an attacker would tamper with.

### `verify_chain` boundary behavior (confirmed by reading the source)
`verify_chain` initializes `prev_hash = "genesis"` and requires the **first** entry's stored
`prev_hash` to equal `"genesis"`. It does **not** anchor on the first entry's own `prev_hash`.
Therefore a naive "fetch latest 1000 + reverse" fix would false-alarm on every run once the table
grows past 1000 rows, because the window's first row's predecessor (`prev_hash`) is some real prior
row hash, not `"genesis"` → `verify_chain` would return `(False, 0)`.

### Fix
1. `audit_chain.verify_chain(entries, *, anchor_first: bool = False)` — new opt-in parameter.
   - Default (`anchor_first=False`): unchanged genesis-anchored behavior (whole-chain verification);
     all pre-existing tests keep passing.
   - `anchor_first=True`: the first entry's *own* stored `prev_hash` is used as the starting anchor.
     The first row's predecessor *link* is trusted (its real predecessor is outside the window), but
     **the first row's own content is still hash-verified** and **every subsequent row is fully
     chained**, so inner tampering and tampering of the first window row's content are both still
     detected.
2. `audit_chain_verify_job` now:
   - `order_by(SignalAudit.id.desc()).limit(1000)` → fetches the **latest** 1000 rows;
   - `list(reversed(result.scalars().all()))` → restores ascending-id chain order before building
     the entries list (so `verify_chain` walks rows in write order);
   - calls `verify_chain(entries, anchor_first=True)` to handle the window edge;
   - preserves the alert-on-break behavior (CRITICAL log + `_send_failure_alert` with
     `AlertType.SERVICE_UNHEALTHY`).

### Boundary documented
The first row in the latest-1000 window has no predecessor inside the window. That is acceptable for
a "recent rows" integrity sweep: its predecessor link is anchored, its content is still verified, and
the rest of the window is a full chain. Whole-chain (genesis) verification remains available via the
default `verify_chain(entries)`.

---

## DEFECT E2 — `hmm_train_job` blocked the event loop

### Root cause
`hmm_train_job` ran CPU-bound `compute_indicators(df)` + `detector.train(df)` (+ `save_model`)
**synchronously on the event loop while holding the DB session** inside the `async with` block. This
stalls every other concurrent scheduler job (scans, paper-tracker, health-check) and needlessly pins
a DB connection during multi-second model training.

### Fix
- **Phase 1 (in session):** load candles per instrument inside `async with session_factory()`,
  build a plain `pandas.DataFrame`, and collect `(symbol, df)` into a `pending` list. The session
  is **released** (context exit) before any CPU work. Per-instrument behavior, the `len(candles) < 600`
  skip, and the `row is None` skip are preserved.
- **Phase 2 (after session released):** offload the blocking pipeline via
  `await asyncio.get_running_loop().run_in_executor(None, _train_hmm_blocking, df, symbol, hmac_key, out)`
  per instrument, then log `"hmm trained"`.
- New module-level helper `_train_hmm_blocking(df, symbol, hmac_key, out_dir)` does
  `compute_indicators → HMMRegimeDetector().train → save_model(...)`. It takes a plain DataFrame and
  primitives only — it never touches the session.
- The `MODEL_HMAC_KEY` (C3) is still threaded into `save_model` via the `hmac_key` argument.
- Added `import asyncio`; pandas/`Path` typing imports placed under `TYPE_CHECKING` to keep the hot
  import path lazy while satisfying mypy strict.

---

## Tests

### `tests/unit/test_audit_chain.py` — `TestVerifyChainAnchor`
- `test_window_not_starting_at_genesis_fails_without_anchor` — default rejects a mid-chain window.
- `test_window_not_starting_at_genesis_ok_with_anchor` — `anchor_first=True` accepts a valid window.
- `test_anchor_first_still_detects_tamper_in_window` — inner tamper still detected at correct index.
- `test_anchor_first_detects_tamper_in_first_window_row` — anchored first row's content still verified.

### `tests/unit/test_scheduler_jobs.py`
- `test_audit_verify_queries_latest_rows_descending` — asserts the SELECT orders by DESC id (latest).
- `test_audit_verify_detects_break_in_latest_rows` — 1001 rows, only the newest (id 1001) tampered;
  the legacy ascending window (id 1..1000) would skip it. Asserts an alert fires.
- `test_audit_verify_no_false_alarm_on_valid_latest_window` — fully valid 1001-row chain must NOT
  false-alarm; guards the anchor-edge handling.
- `test_hmm_train_offloads_to_executor_after_releasing_session` — asserts (a) `_train_hmm_blocking`
  is invoked, (b) the session is closed **before** training runs (event ordering), and (c) training
  runs on a **worker thread** (`threading.get_ident()` differs from the event-loop thread), i.e. it
  was offloaded via `run_in_executor`. A `_boom` guard on `compute`/`HMMRegimeDetector`/`save_model`
  fails the test if any heavy work runs inline on the loop.

The fake `_WindowSession` honors `ORDER BY ... DESC` + a 1000-row limit so the latest-window behavior
is proven without a live DB.

## RED → GREEN evidence
- RED: 6 new tests failed for the right reasons — anchor param missing (TypeError), the audit SELECT
  used ASC, the tampered latest row was not detected (no alert), and the E2 `_boom` guard fired
  ("CPU-bound training ran inline on the event loop"). The two "default/legacy behavior" guard tests
  passed as expected.
- GREEN: after implementing both fixes, all new tests pass.

## Verification
- Targeted: `pytest -q tests/unit/test_audit_chain.py tests/unit/test_scheduler_jobs.py` → all pass.
- Full suite: `.venv\Scripts\pytest.exe -q` → **851 passed, 8 skipped** (integration deselected).
- `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- `.venv\Scripts\mypy.exe src` → Success: no issues found in 129 source files.

## Concerns / follow-ups
- `verify_chain(anchor_first=True)` trades away verification of the window's first-row predecessor
  link (by design — that predecessor is outside the recent-rows window). Whole-chain integrity from
  genesis is still available via the default call and remains the right tool for a full audit; the
  periodic job is a "recent rows" sweep. If a full-history guarantee is ever required on the schedule,
  add a separate (paged) full-chain verification pass.
- `run_in_executor(None, ...)` uses the default thread pool. HMM training releases the GIL only inside
  numpy/sklearn C sections; the offload reliably unblocks the event loop (other coroutines progress),
  which is the goal. If multiple instruments must train truly in parallel, a `ProcessPoolExecutor`
  would be needed — out of scope here.
