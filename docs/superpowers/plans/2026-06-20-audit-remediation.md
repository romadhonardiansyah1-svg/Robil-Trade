# Audit Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the defects found in the full codebase audit (`docs/superpowers/specs/2026-06-20-codebase-audit-findings.md`), restoring trust in the go-live backtest gate, closing the two fail-open safety paths, and hardening security — without regressing the already-clean lint/type/test baseline.

**Architecture:** Pure-function changes are covered by unit tests first (TDD RED→GREEN). Concurrency/DB fixes get focused async tests against the existing test fixtures. Security/config fixes are verified by behavior tests where possible and by manual checklist where not. Each task ends green on `ruff`, `mypy --strict`, and `pytest`.

**Tech Stack:** Python 3.12, pandas/numpy/scipy, SQLAlchemy async + asyncpg, FastAPI, APScheduler, structlog, litellm, pytest + hypothesis.

## Global Constraints (copy verbatim into every task)

- All datetimes MUST be timezone-aware **UTC** (flake8-datetimez `DTZ` is enforced). Never `datetime.now()` without tz; never `replace(tzinfo=UTC)` on a value whose source tz is unknown — convert.
- No `print()` — use `structlog`. (ruff `T20`.)
- `ruff check src tests` and `mypy src` MUST stay clean (`mypy` is `strict`).
- Run the full suite with `.venv\Scripts\pytest.exe -q` (Windows). Tests are `asyncio_mode=auto`.
- **Signal-only system** — never add order execution. Safety gates fail **closed** (block), never open.
- Backtests must never run cost-free as a decision basis.
- Frequent commits: one commit per task (Conventional Commits: `fix:`/`refactor:`/`test:`).
- Branch: do all work on a feature branch `fix/audit-remediation` (NOT `main`). Create a worktree if preferred.

---

## Phase ordering & rationale

1. **Phase 0 — Security quick wins** (C1, C2, C8): low risk, high value, no behavior coupling. Do first.
2. **Phase 1 — Backtest/validation integrity** (A1–A13): the gate is the single biggest risk for a trading system; fixing it may change which strategies "pass", so do it as a block.
3. **Phase 2 — Fail-open safety paths** (B1, B2, B3, B4, B5, B6): prevent trading through news / over budget / over risk.
4. **Phase 3 — Data integrity & concurrency** (D1–D4).
5. **Phase 4 — Reliability & robustness** (E1–E5, C4–C7, C9).
6. **Phase 5 — Numerical/indicator correctness** (F1–F2 + selected lows).
7. **Phase 6 — Low-severity cleanup & dormant modules** (remaining lows, G).

Each task is independently testable and independently reviewable.

---

## Phase 0 — Security quick wins

### Task 0.1: Require API auth token (remove `changeme` default) — C1
**Files:**
- Modify: `config/Caddyfile` (remove `:changeme` default in the `@no_auth` matcher)
- Modify: `docker-compose.prod.yml` (caddy + api env: require `API_AUTH_TOKEN`, no default)
- Verify: `src/rtrade/delivery/api/routes.py:_require_bearer` already 503s on empty token (keep)
- Docs: `.env.prod.example` — document `API_AUTH_TOKEN` as REQUIRED, no default.

**Steps:**
- [ ] Change Caddyfile matcher to `not header Authorization "Bearer {$API_AUTH_TOKEN}"` (no default). Add a comment that an unset var must fail the deploy.
- [ ] In `docker-compose.prod.yml`, set `API_AUTH_TOKEN: ${API_AUTH_TOKEN:?API_AUTH_TOKEN must be set}` for both caddy and api so compose refuses to start when unset.
- [ ] Add a test asserting `_require_bearer` raises 503 when `cfg.secrets.api_auth_token` is falsy (if not already covered).
- [ ] Run `pytest -q tests/...routes...`; manual: `docker compose -f docker-compose.prod.yml config` fails without the var.
- [ ] Commit: `fix(security): require API_AUTH_TOKEN, drop 'changeme' default (C1)`

### Task 0.2: Stop logging OAuth token bodies — C2
**Files:** Modify `src/rtrade/llm/auth/oauth2.py:~224-241`
**Steps:**
- [ ] Replace the token-body log with a redacted log (`status`, `scope`, `expires_in` only). Remove token contents from the `RuntimeError` message (log a generic message; keep status code).
- [ ] Add a test: patch the HTTP exchange to return a known refresh/access token, capture structlog output, assert the token strings never appear in any emitted record or raised exception text.
- [ ] `pytest -q`; commit: `fix(security): never log OAuth token bodies (C2)`

### Task 0.3: Read LLM key from env, not CLI arg — C8
**Files:** Modify `scripts/eval_hallucination.py`
**Steps:**
- [ ] Replace `--api-key` with reading from env (e.g. `GEMINI_API_KEY_1` / config); if a flag must remain, accept `--api-key-env NAME`.
- [ ] Manual run check; commit: `fix(security): take eval key from env, not argv (C8)`

---

## Phase 1 — Backtest / validation integrity

> These tasks share `metrics.py`/`validation.py`/`engine.py`; do them in order. After Phase 1, expect some previously-"passing" backtests to now (correctly) fail gates — that is the point.

### Task 1.1: Apply smart-exit realized P&L — A1
**Files:** Modify `src/rtrade/backtest/smart_exit.py`, `src/rtrade/backtest/engine.py:143-231`; Test `tests/backtest/test_smart_exit.py` (extend) + `tests/backtest/test_engine_smart_exit_pnl.py` (new)
**Interfaces (Produces):** `apply_smart_exit(...) -> (ExitState, exit_reason)` where `ExitState.realized_r` is the cumulative realized R from closed partial legs and `remaining_pct` the open fraction; engine final P&L = `realized_r + remaining_pct * final_leg_r`.
**Steps:**
- [ ] RED: write a test — long, partial 50% at +1R then breakeven stop hit → expected total ≈ +0.5R (not 0R). Assert engine `trade.r_multiple ≈ 0.5`.
- [ ] Run it; confirm it FAILS (currently 0R / full-position).
- [ ] GREEN: in `engine.py`, when `smart_exit` is set, compute `r_multiple = exit_state.realized_r + exit_state.remaining_pct * leg_r(final exit)` and scale `pnl`/`position_size` by remaining fraction for the final leg.
- [ ] Run the test → PASS; run full `tests/backtest`.
- [ ] Commit: `fix(backtest): apply smart-exit partial/realized P&L (A1)`

### Task 1.2: Remove smart-exit intrabar look-ahead — A2
**Files:** Modify `src/rtrade/backtest/smart_exit.py:77-126`; Test extend `tests/backtest/test_smart_exit.py`
**Steps:**
- [ ] RED: a bar where, after a favorable move that *would* raise the trail/BE stop, the same bar's adverse extreme is below the *original* stop. Expect SL hit at the **original/pre-update** stop, not a survived trail.
- [ ] Confirm FAIL.
- [ ] GREEN: reorder — first evaluate SL/TP hit against the stop as of bar entry; only apply BE/trailing updates for *subsequent* bars (or apply update then test against the *minimum* protection, never the favorable one within the same bar).
- [ ] PASS; full `tests/backtest`.
- [ ] Commit: `fix(backtest): pessimistic intrabar ordering in smart-exit (A2)`

### Task 1.3: Correct Sharpe annualization — A3
**Files:** Modify `src/rtrade/backtest/metrics.py:60-67` (and signature to accept trade timestamps or trades/yr); Test extend `tests/backtest/test_metrics.py`
**Interfaces (Produces):** `compute_metrics(..., trades_per_year: float | None=None)`; if None, derive from first/last trade timestamps; per-trade Sharpe annualized by `sqrt(trades_per_year)`.
**Steps:**
- [ ] RED: known R series over a known span (e.g. 50 trades across 1 year) → assert annualized Sharpe = per_trade_sharpe * sqrt(50), not sqrt(252).
- [ ] Confirm FAIL.
- [ ] GREEN: derive trades/yr from timestamps; keep a documented fallback.
- [ ] PASS; commit: `fix(backtest): annualize Sharpe by actual trade frequency (A3)`

### Task 1.4: Fix Deflated Sharpe Ratio gate — A4
**Files:** Modify `src/rtrade/backtest/validation.py:30-86`; Test extend `tests/backtest/test_validation.py`
**Steps:**
- [ ] RED: (a) `expected_max_sharpe` must actually depend on `t_periods` (two different `t_periods` → different output); (b) DSR with `n_trials=1` and a mediocre Sharpe must NOT return ≈1.0; (c) DSR must compare like-for-like Sharpe units (non-annualized).
- [ ] Confirm FAIL.
- [ ] GREEN: use the non-annualized per-trade Sharpe inside DSR; fix `expected_max_sharpe(n_trials, t_periods)` to use `t_periods`; require a real `n_trials` (raise/flag if `n_trials < 2` and DSR is being used as a gate).
- [ ] PASS; commit: `fix(backtest): make DSR gate meaningful (A4)`

### Task 1.5: Compute real PBO; fail closed — A5
**Files:** Modify `src/rtrade/backtest/validation.py:195-197`, `src/rtrade/backtest/permutation.py` or new `pbo.py`, wire in `src/rtrade/backtest/harness.py`; Test new `tests/backtest/test_pbo.py`
**Steps:**
- [ ] RED: CSCV PBO on a known overfit set → PBO high (>0.30, gate fails); on a robust set → low. Insufficient data → gate FAILS (not passes).
- [ ] Confirm FAIL (PBO currently hard-0).
- [ ] GREEN: implement CSCV PBO from walk-forward IS/OOS rank pairs; harness passes the computed value; default/insufficient → `pbo = 1.0` (fail closed).
- [ ] PASS; commit: `fix(backtest): compute CSCV PBO, fail closed (A5)`

### Task 1.6: Charge per-lot round-turn commission — A6
**Files:** Modify `src/rtrade/backtest/costs.py:90-99` (+ call site in `engine.py` to pass position size in lots); Test extend `tests/backtest/test_costs.py`
**Steps:**
- [ ] RED: EURUSD trade with `commission_usd_per_lot_round_turn=7.0` and known lot size → cost includes the commission.
- [ ] Confirm FAIL.
- [ ] GREEN: extend `compute_trade_cost` to add per-lot commission (needs lots); thread lot size from engine.
- [ ] PASS; commit: `fix(backtest): apply per-lot round-turn commission (A6)`

### Task 1.7: Refuse cost-free backtests — A7
**Files:** Modify `src/rtrade/backtest/costs.py` (loader/lookup), `scripts/run_backtest.py`, `src/rtrade/backtest/harness.py`; Test extend `tests/backtest/test_costs.py`
**Steps:**
- [ ] RED: requesting a cost model for an unconfigured symbol raises (or harness refuses to run) instead of silently zero-cost.
- [ ] Confirm FAIL.
- [ ] GREEN: `get_cost_model(symbol)` raises `ConfigError` when missing; harness/CLI surface a clear error. (Optionally add a conservative default profile per market, explicit and logged.)
- [ ] PASS; commit: `fix(backtest): refuse cost-free runs for unconfigured symbols (A7)`

### Task 1.8: Timeframe-aware walk-forward warmup — A8
**Files:** Modify `src/rtrade/backtest/harness.py:186,200`; Test extend `tests/backtest/test_harness.py` (or walkforward test)
**Steps:**
- [ ] RED: D1 walk-forward with `warmup_bars=200` reserves ≥200 *days*, and OOS signals never use warmup-zone bars.
- [ ] Confirm FAIL.
- [ ] GREEN: `warmup_start = train_end_ts - warmup_bars * timeframe_to_timedelta(tf)`.
- [ ] PASS; commit: `fix(backtest): size warmup by timeframe (A8)`

### Task 1.9: Gap-aware fills — A9
**Files:** Modify `src/rtrade/backtest/engine.py:172-205`; Test extend `tests/backtest/test_engine.py`
**Steps:**
- [ ] RED: a bar that opens beyond the stop → exit booked at the gapped open (worse than SL for the trade), not at the exact SL.
- [ ] Confirm FAIL.
- [ ] GREEN: on a bar whose open is already past the level, fill at the open; else at the level.
- [ ] PASS; commit: `fix(backtest): gap-aware SL/TP fills (A9)`

### Task 1.10: Backtest metrics edge cases — A11, A12, A13
**Files:** Modify `src/rtrade/backtest/metrics.py:57-61`, `permutation.py:46-54`; Test extend metrics/permutation tests
**Steps:**
- [ ] RED: (A11) permutation p-value uses `(count+1)/(n+1)` (never exactly 0); (A12) R==0 trades are neutral, not losses; (A13) `profit_factor` with zero losses returns a large finite sentinel or the gate treats `inf` as needing ≥N losing trades (don't trivially pass Gate 3).
- [ ] Confirm FAIL; GREEN; PASS.
- [ ] Commit: `fix(backtest): metrics/permutation edge cases (A11-A13)`

### Task 1.11 (optional, larger): time-ordered equity curve — A10
**Files:** Modify `src/rtrade/backtest/engine.py:113-231`; Test new equity-curve test
**Steps:**
- [ ] Decide with reviewer: implement a shared-timeline equity curve OR document single-position assumption and assert no-overlap. RED→GREEN per chosen path.
- [ ] Commit: `fix(backtest): time-ordered equity for overlapping trades (A10)` (or `docs:` if deferred)

---

## Phase 2 — Fail-open safety paths

### Task 2.1: Calendar empty-success fail-open — B1
**Files:** Modify `src/rtrade/data/composite_calendar.py`; Test extend `tests/...composite_calendar...`
**Steps:**
- [ ] RED: source A returns `[]` while source B has events → composite must fail over to B (not record A as success and stop). All-empty across sources → mark NOT-fresh / raise so the staleness gate blocks.
- [ ] Confirm FAIL.
- [ ] GREEN: only record `last_success` on a validated non-degenerate response; failover on empty when others remain; emit `DATA_GAP` alert on all-empty.
- [ ] PASS; commit: `fix(safety): calendar empty response no longer counts as success (B1)`

### Task 2.2: Persistent daily LLM budget — B2
**Files:** Modify `src/rtrade/llm/budget_guard.py`, call site `src/rtrade/pipeline/scan.py:~1110`, and `src/rtrade/llm/key_manager.py` (wire `report_cost`/`get_daily_cost`); Test extend budget tests
**Steps:**
- [ ] RED: two consecutive scans whose combined USD exceeds `max_usd_per_day` → the second scan trips `usd_day` budget_stop.
- [ ] Confirm FAIL (currently resets each scan).
- [ ] GREEN: seed `BudgetState.day_usd` from a persistent store (Redis/DB or `KeyManager.get_daily_cost`) at `start_scan`; call `report_cost` on every spend. Keep reset keyed on UTC date.
- [ ] PASS; commit: `fix(safety): enforce daily LLM budget across scans (B2)`

### Task 2.3: Position-size risk-cap respected — B3
**Files:** Modify `src/rtrade/risk/sizing.py` (`compute_position_size`); Test extend `tests/...sizing...`
**Steps:**
- [ ] RED: when the min-lot floor would exceed `risk_pct`, function returns 0 / abstain (and reports true USD risk), never silently over-risks.
- [ ] Confirm FAIL; GREEN; PASS.
- [ ] Commit: `fix(risk): abstain when min-lot exceeds risk cap (B3)`

### Task 2.4: Cap and report Kelly risk — B4
**Files:** Modify `src/rtrade/risk/sizing.py` (`compute_with_kelly`); Test extend
**Steps:**
- [ ] RED: high-edge inputs → Kelly-implied risk is clamped to GR-05 cap; returned `risk_amount_usd` equals the actual risk used.
- [ ] Confirm FAIL; GREEN; PASS.
- [ ] Commit: `fix(risk): clamp + correctly report Kelly risk (B4)`

### Task 2.5: News-filter timezone correctness — B5
**Files:** Modify `src/rtrade/risk/news_filter.py` (+ provider tz from calendar layer); Test extend news_filter tests
**Steps:**
- [ ] RED: an event provided in a non-UTC tz lands in the correct UTC blackout window (no `replace(tzinfo=UTC)` on naive provider-local times).
- [ ] Confirm FAIL; GREEN (convert via declared provider tz; validate on ingest); PASS.
- [ ] Commit: `fix(safety): convert (not stamp) calendar event tz (B5)`

### Task 2.6: Guardrails fail closed on missing inputs — B6
**Files:** Modify `src/rtrade/guardrails/gate.py`, `src/rtrade/guardrails/selftest.py`; Test extend gate tests
**Steps:**
- [ ] RED: calling the gate with a required input omitted → BLOCK + audit failure (not skip). selftest covers the missing-input path.
- [ ] Confirm FAIL; GREEN (require inputs; remove `if x is not None` skip semantics for required gates); PASS — and re-run `scan.py` integration test to confirm prod still passes all inputs.
- [ ] Commit: `fix(safety): guardrails fail closed on missing inputs (B6)`

### Task 2.7: UTC for regime rules timestamp — B7
**Files:** Modify `src/rtrade/regime/rules.py`; Test extend
**Steps:**
- [ ] RED: `now=None` path yields a tz-aware UTC datetime. GREEN via `ensure_utc`. Commit: `fix: tz-aware UTC in regime rules (B7)`

---

## Phase 3 — Data integrity & concurrency

### Task 3.1: Serialize audit-chain appends — D1
**Files:** Modify `src/rtrade/persistence/repositories.py` (`AuditRepo.add`); Test new async concurrency test in `tests/integration` (marked `integration`) or a serialized-unit test
**Steps:**
- [ ] RED: two concurrent `AuditRepo.add` against the same parent must produce a single linear chain (no fork) — verify with `verify_chain`.
- [ ] Confirm FAIL.
- [ ] GREEN: take `pg_advisory_xact_lock(<chain_key>)` before the read-then-insert (or `SELECT ... FOR UPDATE` on a chain-head row). Keep it within the caller's transaction.
- [ ] PASS; commit: `fix(audit): serialize hash-chain appends (D1)`

### Task 3.2: OANDA UTC candle alignment — D2
**Files:** Modify `src/rtrade/data/oanda_provider.py` (`fetch_ohlcv`); Test extend oanda provider test (respx)
**Steps:**
- [ ] RED: the request for H4/D1 includes `alignmentTimezone=UTC` & `dailyAlignment=0` (assert on the mocked request params); returned bars land on the UTC grid.
- [ ] Confirm FAIL; GREEN; PASS.
- [ ] Commit: `fix(data): UTC candle alignment for OANDA (D2)`

### Task 3.3: Audit verify checks the latest rows — D3
**Files:** Modify `src/rtrade/scheduler/jobs.py` (`audit_chain_verify_job`); Test extend scheduler/jobs test
**Steps:**
- [ ] RED: with >1000 audit rows, a break in the newest rows is detected.
- [ ] Confirm FAIL; GREEN (`ORDER BY id DESC LIMIT 1000`, reverse to ascending, seed `prev_hash` from the row before the window); PASS.
- [ ] Commit: `fix(audit): verify the latest 1000 rows, not the first (D3)`

### Task 3.4: Timeframe-aware gap detection — D4
**Files:** Modify `src/rtrade/data/ingestion.py` (`detect_candle_gaps`); Test extend ingestion test
**Steps:**
- [ ] RED: D1 multi-week gap is flagged; M5 normal weekend is not over-flagged. GREEN (threshold from timeframe duration). PASS.
- [ ] Commit: `fix(data): timeframe-aware candle-gap heuristic (D4)`

---

## Phase 4 — Reliability, robustness, remaining security

### Task 4.1: Reuse DB engine in API — E1
**Files:** Modify `src/rtrade/delivery/api/routes.py` (use shared `_get_engine`/session-factory via FastAPI dependency), `src/rtrade/delivery/api/__init__.py` if needed; Test extend api tests
**Steps:**
- [ ] RED: a test asserting handlers use the shared factory (e.g. patch `_get_engine` and assert no per-request `create_engine`/`dispose`).
- [ ] Confirm FAIL; GREEN (dependency that yields a session from the singleton factory; dispose only on app shutdown); PASS.
- [ ] Commit: `fix(api): reuse shared engine/session factory (E1)`

### Task 4.2: Offload HMM training from the event loop — E2
**Files:** Modify `src/rtrade/scheduler/jobs.py` (`hmm_train_job`); Test extend
**Steps:**
- [ ] Load candles in the session, then `await asyncio.get_running_loop().run_in_executor(None, _train_blocking, df)` outside the session scope. Save model after.
- [ ] Test: training runs without holding the session; commit: `fix(scheduler): run HMM training in executor (E2)`

### Task 4.3: Safe alert formatting — E3
**Files:** Modify `src/rtrade/monitoring/alerts.py` (`_format_alert`/`_send_telegram`); Test extend alerts test
**Steps:**
- [ ] RED: an error string containing Markdown specials (`_ * [ ] ( ) ` `) is delivered without a Telegram 400 (escape or `parse_mode=None`).
- [ ] Confirm FAIL; GREEN (MarkdownV2 escaping helper, or drop parse_mode for dynamic bodies); PASS.
- [ ] Commit: `fix(monitoring): escape dynamic content in Telegram alerts (E3)`

### Task 4.4: Timeframe-aware backfill pagination — E4
**Files:** Modify `src/rtrade/cli/backfill.py:~120`; Test new small unit test for the cursor-advance helper
**Steps:**
- [ ] Extract `advance_cursor(since, tf, batch=499)` using `timeframe_to_timedelta(tf)`. RED test for D1/M5/M15/H4; GREEN; PASS.
- [ ] Commit: `fix(cli): timeframe-aware backfill pagination (E4)`

### Task 4.5: Composite spread per-leg failover — E5
**Files:** Modify `src/rtrade/data/composite_market.py` (`fetch_spread`); Test extend
**Steps:**
- [ ] RED: first leg raises → second leg used; GREEN (per-leg try/except + failover); PASS. Commit: `fix(data): per-leg failover in composite spread (E5)`

### Task 4.6: Split public liveness vs authenticated health — C4
**Files:** Modify `src/rtrade/delivery/api/routes.py`, `config/Caddyfile`; Test extend api tests
**Steps:**
- [ ] RED: public `/health` returns only `{"status": ...}` with no version/used_memory/last_error; detailed health requires bearer.
- [ ] Confirm FAIL; GREEN; PASS. Commit: `fix(security): minimal public health, detailed health behind auth (C4)`

### Task 4.7: Trust proxy hop for client IP; bound the failure map — C5
**Files:** Modify `src/rtrade/delivery/api/routes.py:_client_ip`, `_auth_failures`; Test extend
**Steps:**
- [ ] RED: spoofed XFF cannot reset another IP's limiter; map entries are evicted by TTL/size.
- [ ] GREEN (take the last/rightmost XFF entry from the trusted proxy, or a configured trusted-proxy count; TTL eviction); PASS.
- [ ] Commit: `fix(security): robust client-IP + bounded auth-failure map (C5)`

### Task 4.8: Complete prompt-injection defenses — C6
**Files:** Modify `src/rtrade/llm/context_pack.py`, `src/rtrade/llm/sanitize.py`, prompt builders; Test extend llm tests
**Steps:**
- [ ] RED: an event field containing injection text is sanitized AND wrapped in the `<DATA_TIDAK_TEPERCAYA>` delimiter in `to_prompt_text()`.
- [ ] GREEN (sanitize all untrusted fields; apply the delimiter the system prompt references); PASS.
- [ ] Commit: `fix(security): sanitize+delimit all untrusted prompt data (C6)`

### Task 4.9: Bound device-code OAuth polling — C7
**Files:** Modify `src/rtrade/llm/auth/oauth2.py` (`device_login`); Test extend
**Steps:**
- [ ] RED: polling stops at `expires_in` / max attempts; GREEN; PASS. Commit: `fix(security): bound device-code polling (C7)`

### Task 4.10: Recursive, broader log redaction — C9
**Files:** Modify `src/rtrade/core/logging_redact.py`; Test extend redaction test
**Steps:**
- [ ] RED: nested dict/list secrets redacted; URLs with `token=`/`api_key=`/`apikey=` redacted; keys `authorization`/`refresh_token`/`secret` redacted.
- [ ] GREEN (recurse structures; broaden patterns); PASS. Commit: `fix(security): recursive log redaction + more key patterns (C9)`

### Task 4.11: Model integrity via keyed HMAC — C3
**Files:** Modify `src/rtrade/ml/model_io.py`; Test extend model_io test
**Steps:**
- [ ] RED: tampering with the model file (without the secret) fails verification; recomputing the plain hash does NOT pass.
- [ ] GREEN (HMAC-SHA256 with a secret from config/env, not stored beside the model; verify before `joblib.load`). Document that loading remains pickle-based and the HMAC is the trust boundary.
- [ ] PASS; commit: `fix(security): keyed-HMAC model integrity (C3)`

---

## Phase 5 — Numerical / indicator correctness

### Task 5.1: Daily-anchored VWAP — F1
**Files:** Modify `src/rtrade/indicators/engine.py:119-126`; Test extend indicators test
**Steps:**
- [ ] RED: VWAP resets at UTC midnight (first bar of a day → VWAP == typical price of that bar). GREEN (group by UTC date); PASS.
- [ ] Commit: `fix(indicators): UTC-daily-anchored VWAP (F1)`

### Task 5.2: Make `compute()` non-mutating — F2
**Files:** Modify `src/rtrade/indicators/engine.py:73-141`; Test extend
**Steps:**
- [ ] RED: caller's DataFrame is unchanged after `compute(df)` (same columns, same dtypes). GREEN (`df = df.copy()` at entry); PASS.
- [ ] Commit: `fix(indicators): compute() no longer mutates input (F2)`

### Task 5.3: Selected signal-quality lows — F3, F4, F5
**Files:** `src/rtrade/signals/levels.py:19-30`, `src/rtrade/signals/confluence.py:119-141,178-188`; Tests extend
**Steps:**
- [ ] RED for each: tick rounding to arbitrary tick size (0.25) lands on grid; volume SMA excludes current bar (consistent with `edge_quality`); "nearest level" selects min distance to entry.
- [ ] GREEN; PASS. Commit: `fix(signals): tick rounding, volume window, nearest-level (F3-F5)`

---

## Phase 6 — Low-severity cleanup & dormant modules

### Task 6.1: Structure detection lows — F6, F7
- [ ] Make S/R clustering order-independent (cluster on sorted levels with fixed tolerance); allow equal-high/low swing detection (double tops/bottoms). RED→GREEN→commit.

### Task 6.2: Persistence lows — D5
- [ ] Replace `EventRepo.upsert_many` merge-per-row with batched `ON CONFLICT`; make `get_or_create`/`set_state`/calendar upsert use `ON CONFLICT` to remove TOCTOU. Stop mutating caller's `detail` dict in `AuditRepo.add`. RED→GREEN→commit.

### Task 6.3: Infra hygiene — E6, plus CI/Docker
- [ ] `backup_db.sh`: add `set -euo pipefail`. `setup_vps.sh`: pin Docker install (avoid `curl|sh` as root, or checksum). Healthcheck DB/Redis connects: add timeouts. CI: add `permissions:` block; pin `uv` image tag in Dockerfile. Commit per file group.

### Task 6.4: Dormant ML correctness — G
- [ ] `meta_label.py`: carry `outcome_r` through `prepare_labels`; compute expectancy OOS (not refit-on-all). `similar.py`: cyclic-encode hour-of-day. Mark module status. RED→GREEN→commit. (Lower priority — module is dormant; do only if it will be relied upon.)

---

## Self-review (completed)

- **Spec coverage:** every A/B/C/D/E/F/G finding in the audit maps to a task above (A1–A13 → 1.1–1.11; B1–B7 → 2.1–2.7; C1–C9 → 0.1/0.2/4.6/4.7/4.8/4.9/4.10/4.11; D1–D5 → 3.1–3.4 + 6.2; E1–E6 → 4.1–4.5 + 6.3; F1–F7 → 5.x + 6.1; G → 6.4).
- **Fail-closed posture:** A5, A7, B1, B6 explicitly default to the safe/blocking outcome.
- **Consistency:** Sharpe-unit alignment is shared between Task 1.3 and 1.4 — 1.4 depends on 1.3, ordered accordingly. Cost-model lot threading is shared between 1.6 and 1.7.
- **No placeholders:** each task names exact files, the RED test intent, the GREEN change, and a commit message.

## Execution handoff

Plan saved. Two execution options:
1. **Subagent-driven (recommended):** one fresh subagent per task, review between tasks. Best for this size (~35 tasks).
2. **Inline execution:** batch with checkpoints at phase boundaries.

Recommended order if time-boxed: **Phase 0 → Phase 1 → Phase 2** first (security + gate integrity + fail-open safety) — that covers all 13 High findings and the highest-value Mediums.
