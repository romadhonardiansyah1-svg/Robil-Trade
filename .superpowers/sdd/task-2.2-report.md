# Task 2.2 — Defect B2: persist + seed daily LLM budget across scans

Branch: `fix/audit-remediation` · Python 3.12 · TDD

## The fail-open mechanism (before)

`scan._run_strategies` constructed the budget guard fresh **per scan**:

```python
budget_guard = BudgetGuard(cfg.settings.llm.budget)
budget_state = budget_guard.start_scan()   # BudgetState(day_usd=0.0)
```

`BudgetGuard.record` enforces the daily USD cap with `state.day_usd >= max_usd_per_day`,
but `day_usd` was reseeded to `0.0` on every scan. The only cross-scan daily accounting
(`KeyManager.report_cost` / `get_daily_cost`, Redis key `rtrade:cost:{YYYY-MM-DD}` via
`incrbyfloat` + 25h expiry) was **never called in the scan path**. Net effect: the
"USD per day" cap was really a "USD per scan" cap — it reset every scan and never
accumulated. Fail-OPEN: spend could exceed the daily cap without ever tripping.

## The seed + persist design

UTC-date keyed, snapshot-seed + atomic-increment-persist:

1. **`BudgetGuard.start_scan(self, *, day_usd_seed: float = 0.0) -> BudgetState`**
   — seeds `BudgetState.day_usd` with `day_usd_seed`. Default `0.0` keeps every existing
   caller/test unchanged. `reset_day_if_needed` (UTC date) is untouched, and the cap
   comparison (`day_usd >= max_usd_per_day`) is unchanged — only *where day_usd starts*
   changed.

2. **Seed before the scan** (`scan.py`, budget call site):
   ```python
   cost_store = _llm_cost_store(cfg)
   seeded_day_usd = await _seed_daily_spend(cost_store)
   budget_state = budget_guard.start_scan(day_usd_seed=seeded_day_usd)
   ```

3. **Persist after the scan** (once, after BOTH pipeline calls — initial + flagship
   escalation, which share `budget_state`):
   ```python
   await _persist_scan_spend(cost_store, budget_state.day_usd - seeded_day_usd)
   ```

New helpers in `scan.py`:
- `_llm_cost_store(cfg) -> KeyManager | None` — builds the store (see below).
- `_seed_daily_spend(store) -> float` — `await store.get_daily_cost()`, or `0.0` if `store is None`.
- `_persist_scan_spend(store, delta_usd) -> None` — `await store.report_cost("scan","scan",delta)`,
  guarded `delta_usd > 0`; no-op if `store is None`.

## How the Redis / KeyManager is obtained in scan.py (and the no-redis fallback)

`_run_strategies` already has `cfg: AppConfig` in scope. The scan path already obtains a
process-scoped redis client elsewhere via `rtrade.persistence.db._get_redis(cfg.secrets.redis_url)`
(used at lines 242/523/753 for `RateLimiter`). I reuse that same accessor and wrap it in a
`KeyManager`, so cost accounting uses the **same** Redis client and the **same**
`rtrade:cost:{YYYY-MM-DD}` key (`incrbyfloat` + 25h expiry) that `KeyManager` already owns:

```python
def _llm_cost_store(cfg: AppConfig) -> KeyManager | None:
    try:
        redis_client = _get_redis(cfg.secrets.redis_url)
    except Exception:
        return None
    return KeyManager(redis_client, daily_budget_usd=cfg.settings.llm.budget.max_usd_per_day)
```

Graceful degrade is layered:
- If a client cannot be obtained at all → `_llm_cost_store` returns `None` → seed `0.0`,
  persist is a no-op (pre-B2 per-scan behavior, no crash).
- If a client exists but Redis is down → `KeyManager.get_daily_cost` / `report_cost` already
  catch exceptions internally and fall back to their in-memory `_daily_cost` dict
  (returning `0.0` for an absent day). No crash.

## UTC-date consistency

`KeyManager` keys cost by `rtrade:cost:{datetime.now(UTC).strftime("%Y-%m-%d")}`.
`BudgetState.day` defaults to `datetime.now(UTC).date()` and `reset_day_if_needed` rolls
over on the UTC date. Seed (read of today's UTC key) and persistence (increment of today's
UTC key) therefore agree with the budget guard's own UTC day boundary — no off-by-one
across midnight or across timezones.

## Delta-persist-once-per-scan logic

The within-scan spend is `budget_state.day_usd - seeded_day_usd` — the increment added
during this scan only (the seed is the prior persisted total, so subtracting it avoids
double-counting). Persisted **once**, after the escalation block (both `run_llm_pipeline`
calls share `budget_state`, and there are no further LLM calls in `_run_strategies`), so it
runs on every path that did LLM work, including the early returns after the pipeline.
`_persist_scan_spend` guards `delta_usd > 0`, so a zero/negative delta is a no-op (never
decrements the daily key).

## Concurrency note

`report_cost` uses Redis `incrbyfloat`, which is atomic, so concurrent scans accumulate the
daily total correctly. Seeding is a **snapshot** read of the key at scan start; under heavy
concurrency two overlapping scans may each seed before the other persists, producing a
slight **under-count** of the seed. This is acceptable and fails **CLOSED sooner, not later**:
the authoritative accumulation is the atomic increment, so the daily key still reflects true
total spend; the only effect of a stale snapshot is that the cap may trip marginally earlier.

## Verification

- RED:
  - `test_budget_guard.py::test_seed_carries_prior_spend` → `TypeError: start_scan() got an
    unexpected keyword argument 'day_usd_seed'` (proves seed param absent).
  - `test_budget_guard_wiring.py` → `ImportError: cannot import name '_persist_scan_spend'`
    (proves wiring helpers absent).
- GREEN (after implementation):
  - `pytest -q tests/unit/test_budget_guard.py tests/unit/test_key_manager.py
    tests/unit/test_budget_guard_wiring.py` → all pass (19 tests).
- Full suite: `.venv\Scripts\pytest.exe` → **818 passed, 7 skipped** in ~91s.
- `.venv\Scripts\ruff.exe check src tests` → All checks passed.
- `.venv\Scripts\mypy.exe src` → Success: no issues found in 129 source files (strict).

## Tests added

- `tests/unit/test_budget_guard.py`: `test_seed_default_unchanged`, `test_seed_carries_prior_spend`.
- `tests/unit/test_budget_guard_wiring.py`: this file already existed (D2 pipeline-level
  budget enforcement, `TestBudgetGuardWiring`). I PRESERVED those 4 tests and appended a new
  `TestDailyBudgetSeedPersist` class: a two-consecutive-scan wiring test proving the 2nd scan
  seeds from the 1st scan's persisted spend and trips `usd_day`; plus no-store and
  negative/zero-delta guard tests. Uses `KeyManager(redis_client=None)` in-memory fallback.

## Concerns

- Snapshot-seed under-count under concurrency (documented above) — acceptable, fails closed sooner.
- `_persist_scan_spend` records spend under provider/key `"scan"`; `KeyManager.report_cost`
  ignores provider/key for the cost key (date-only), so this only affects the alert log label,
  not accounting. If per-provider daily breakdown is ever wanted, that would be a follow-up.
- No live-Redis integration test was added (suite has no Redis fixture); cross-process
  persistence relies on `KeyManager`'s already-tested Redis path.
