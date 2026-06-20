# Robil Trade — Full Codebase Audit Findings

**Date:** 2026-06-20
**Scope:** Entire repository (`src/rtrade/**`, `config/**`, `scripts/**`, `migrations/**`, Docker/CI/infra).
**Method:** 7 parallel read-only domain audits + manual confirmation of all High/Critical findings against source.
**Baseline:** ruff ✅ clean · mypy --strict ✅ clean (129 files) · pytest ✅ all pass (116 test files). All findings below are defects the existing tooling does **not** catch (logic, financial-correctness, concurrency, security, fail-open behavior).

> This is a **signal-only** trading assistant (no order execution). The highest-impact class of bug here is anything that (a) makes the go-live backtest gate pass when it shouldn't, (b) lets the bot publish signals during news blackout / over budget, or (c) leaks secrets. Those are prioritized accordingly.

---

## Severity legend

- **Critical** — silent financial-decision corruption or secret exposure in normal operation.
- **High** — wrong results / safety bypass under realistic conditions.
- **Medium** — wrong results under specific configs, or robustness/DoS.
- **Low** — latent, defense-in-depth, or cosmetic-but-real.

---

## A. Backtest & Validation Integrity (go-live gate is currently unreliable)

### [HIGH] A1 — Smart-exit P&L is computed but never applied
- **File:** `src/rtrade/backtest/engine.py:143-231`, `src/rtrade/backtest/smart_exit.py:77-126`
- **Problem:** `ExitState.realized_r` / `remaining_pct` (partial TP, breakeven, trailing) are tracked but P&L is always computed on the **full** position from `fill_price → exit_price`. The smart-exit modeled outcome is discarded.
- **Impact:** Every `smart_exits=True` backtest reports numbers that don't match the modeled exits (e.g. partial+breakeven that should be +0.5R reports 0R). Go-live metrics are wrong.
- **Fix:** Make P&L the sum of realized partial legs + remaining leg at final exit; have `apply_smart_exit` return the realized R contribution per close.

### [HIGH] A2 — Smart-exit intrabar look-ahead optimism
- **File:** `src/rtrade/backtest/smart_exit.py:77-126`
- **Problem:** Within one bar, SL is moved to breakeven/trailing using the bar's **favorable** extreme *before* checking whether the bar's adverse extreme already hit the (raised) stop. Assumes high precedes low.
- **Impact:** Inflates results; the plain path (`engine.py:188-199`) is correctly SL-first pessimistic, so smart-exit results are systematically optimistic.
- **Fix:** Check adverse-extreme stop hit against the *pre-update* stop first; only then apply trailing/BE for subsequent bars.

### [HIGH] A3 — Sharpe annualized with hard-coded √252 on per-trade R
- **File:** `src/rtrade/backtest/metrics.py:60-67`
- **Problem:** `sharpe = mean_r/std_r * sqrt(periods_per_year)` applied to **per-trade** R-multiples, with `periods_per_year` defaulting to 252.
- **Impact:** Overstates Sharpe by √(252 / actual_trades_per_year); this Sharpe feeds the DSR gate (A4).
- **Fix:** Annualize using actual trades-per-year derived from the trade timestamps, or report per-trade Sharpe and annualize explicitly with the real frequency.

### [HIGH] A4 — Deflated Sharpe Ratio gate is a rubber stamp
- **File:** `src/rtrade/backtest/validation.py:30-44, 60-86, 185-197`
- **Problem:** DSR compares the **annualized** Sharpe (A3) against a non-annualized `SR0`/`SE`, yielding z≈15 → `dsr_prob≈1.0 ≥ 0.90` always. `n_trials` defaults to 1, forcing `SR0=0`. `expected_max_sharpe` ignores its `t_periods` arg.
- **Impact:** Gate 5 ("dsr_prob ≥ 0.90") always passes — no overfitting deflation.
- **Fix:** Use consistent (non-annualized) Sharpe units in DSR; require a real `n_trials` (number of configs tried); fix `expected_max_sharpe` to use `t_periods`.

### [HIGH] A5 — PBO gate is vacuous
- **File:** `src/rtrade/backtest/validation.py:195-197`, `src/rtrade/backtest/harness.py`
- **Problem:** `pbo_val = pbo_value if pbo_value is not None else 0.0`; harness never computes real PBO, so Gate 6 (`pbo ≤ 0.30`) always passes. The PBO function also fails open on insufficient data.
- **Impact:** Probability-of-backtest-overfitting check does nothing.
- **Fix:** Compute PBO from the walk-forward splits (CSCV) and pass it in; fail closed on insufficient data.

### [HIGH] A6 — Round-turn per-lot commission never charged
- **File:** `src/rtrade/backtest/costs.py:48-55, 90-99`
- **Problem:** `commission_usd_per_lot_round_turn` is loaded into `CostModel` but `compute_trade_cost` only returns `pct_cost + pip_cost`. EURUSD's `$7/lot` commission is silently dropped.
- **Impact:** Understates forex costs → optimistic backtest.
- **Fix:** Add per-lot commission to the cost computation (requires position size in lots at the call site).

### [HIGH] A7 — Some instruments backtest with ZERO cost
- **File:** `config/costs.yaml`, `scripts/run_backtest.py`, `src/rtrade/backtest/costs.py:90-99`
- **Problem:** `costs.yaml` only defines XAUUSD/EURUSD/BTCUSDT. Any other symbol (GBPUSD/USDJPY/ETHUSDT, etc.) resolves to no model → zero cost, despite the project rule banning cost-free backtests as a decision basis.
- **Impact:** Cost-free backtests can pass the go-live gate.
- **Fix:** Fail closed when a symbol has no cost model (raise/refuse), or require an explicit conservative default.

### [MEDIUM] A8 — Walk-forward warmup sized in hours regardless of timeframe
- **File:** `src/rtrade/backtest/harness.py:186, 200`
- **Problem:** `warmup_start = train_end_ts - pd.Timedelta(hours=warmup_bars)` assumes 1 bar = 1 hour. On D1, warmup is far too short for EMA200 → contaminates OOS.
- **Fix:** Size warmup by `warmup_bars × timeframe_duration`.

### [MEDIUM] A9 — SL/TP filled at exact level even on gaps
- **File:** `src/rtrade/backtest/engine.py:172-205`
- **Problem:** On a bar that gaps through the stop, exit is booked at the exact SL/TP, not the gapped open.
- **Impact:** Understates stop losses.
- **Fix:** Fill at `min/max(level, bar_open)` per direction when the bar opens beyond the level.

### [MEDIUM] A10 — Sequential equity compounding ignores concurrent trades
- **File:** `src/rtrade/backtest/engine.py:113-231`
- **Problem:** Equity compounds trade-by-trade sequentially even when trades overlap in time.
- **Impact:** Distorts equity curve, max drawdown, and Sharpe.
- **Fix:** Build a time-ordered equity curve (mark-to-trade-close on a shared timeline) or document/forbid overlap.

### [LOW] A11 — permutation p-value can be exactly 0
- **File:** `src/rtrade/backtest/permutation.py:46-54` — use `(count+1)/(n+1)` correction.

### [LOW] A12 — R=0 trades counted as losses
- **File:** `src/rtrade/backtest/metrics.py:57-58`.

### [LOW] A13 — profit_factor returns `inf` when no losses → trivially passes Gate 3
- **File:** `src/rtrade/backtest/metrics.py:60-61`.

---

## B. Trading-Safety Correctness

### [HIGH] B1 — News-blackout fail-open: empty calendar treated as success
- **File:** `src/rtrade/data/composite_calendar.py` (`fetch_events`)
- **Problem:** An empty list from the first source records `last_success` and stops failover. A silently-broken source (schema drift → `[]`) then reports "fresh" with zero events; the staleness gate (`scan.py`) keys off `last_success`.
- **Impact:** **Real fail-open** — the bot can publish signals straight through FOMC/NFP/CPI.
- **Fix:** Treat empty-but-"successful" responses as suspect: only mark success on a positively-validated payload; failover on empty when other sources exist; alert on all-empty.

### [HIGH] B2 — LLM daily USD budget cap is effectively per-scan
- **File:** `src/rtrade/llm/budget_guard.py:38-44`, call site `src/rtrade/pipeline/scan.py:~1110`
- **Problem:** `start_scan()` returns a fresh `BudgetState(day_usd=0.0)`; scan.py builds a new `BudgetGuard`+`start_scan()` per scan. `KeyManager.report_cost`/`get_daily_cost` (the only cross-scan daily accounting) is never called.
- **Impact:** `max_usd_per_day` never accumulates across scans → daily cost ceiling unenforced.
- **Fix:** Persist daily spend (DB/Redis) and seed `day_usd` from it at scan start; or centralize accounting in `KeyManager` and consult it in the guard.

### [MEDIUM] B3 — Position size rounding can exceed the risk cap
- **File:** `src/rtrade/risk/sizing.py` (`compute_position_size`)
- **Problem:** Min lot-step floor bumps sub-minimum sizes up to one lot, exceeding `risk_pct`/GR-05 while still reporting the smaller intended `risk_amount_usd`.
- **Fix:** When the floored size exceeds the risk cap, abstain (return 0 / reject) rather than silently over-risking; report the true USD risk.

### [MEDIUM] B4 — Kelly suggestion is uncapped and misreported
- **File:** `src/rtrade/risk/sizing.py` (`compute_with_kelly`)
- **Problem:** `kelly_risk = equity*kelly_f` is uncapped (quarter-Kelly can still imply 5–15% risk ≫ 2% GR-05), and `risk_amount_usd` reflects only the base size.
- **Fix:** Clamp Kelly risk to the GR-05 cap; report the actual USD risk used.

### [MEDIUM] B5 — News-filter stamps naive event times as UTC instead of converting
- **File:** `src/rtrade/risk/news_filter.py`
- **Problem:** Naive/offset-less event times get `replace(tzinfo=UTC)`. If the calendar provider emits local/exchange time, the blackout window is offset by hours.
- **Impact:** Signals can publish into NFP/CPI/FOMC. (Contingent on provider tz — see also A-domain calendar findings.)
- **Fix:** Convert using the provider's declared tz; validate provider tz on ingest.

### [MEDIUM] B6 — Guardrails fail OPEN on missing inputs
- **File:** `src/rtrade/guardrails/gate.py`
- **Problem:** GR-07/08/09/13 are wrapped in `if input is not None`, GR-12 defaults `signals_today=0`. Omitting an input **skips** the check with no audit failure. `selftest.py` only tests the inputs-present path.
- **Impact:** A caller that drops an input silently disables a safety gate (latent; prod `scan.py` currently passes them).
- **Fix:** Require inputs; fail closed (block + audit) when a gate's input is absent. Add selftest coverage for the missing-input path.

### [LOW] B7 — `regime/rules.py` can produce naive datetime (golden-rule UTC) — route through `ensure_utc`.

---

## C. Security

### [HIGH] C1 — Default API auth token `changeme`
- **File:** `config/Caddyfile` (`{$API_AUTH_TOKEN:changeme}`), `docker-compose.prod.yml` (caddy env)
- **Problem:** If `API_AUTH_TOKEN` is unset, the reverse proxy accepts `Bearer changeme` for all non-health routes.
- **Impact:** Auth bypass on `/signals`, `/scan`, `/metrics`, `/analytics/*`.
- **Fix:** Remove the default; fail to start (or return 503) when the token is unset. The app layer (`routes.py`) already 503s on empty token — make the proxy consistent and require the env at deploy.

### [HIGH] C2 — OAuth token-exchange body logged (secret leak)
- **File:** `src/rtrade/llm/auth/oauth2.py:224-229` (and `:241` in `RuntimeError`)
- **Problem:** Full token response (access_token + refresh_token) is logged via structlog and embedded in an exception message.
- **Impact:** Long-lived refresh tokens land in logs.
- **Fix:** Never log token bodies; redact to status + scopes only.

### [HIGH] C3 — Model integrity sidecar gives no real tamper protection
- **File:** `src/rtrade/ml/model_io.py`
- **Problem:** Integrity = unkeyed SHA-256 plaintext sidecar next to the model. An attacker who can overwrite the model recomputes the sidecar; `joblib.load` still executes arbitrary code (pickle RCE).
- **Impact:** The documented threat (untrusted/tampered model) is not mitigated.
- **Fix:** Keyed HMAC with a secret not stored beside the model, or signature verification; consider `skops`/safetensors for the actual estimators.

### [MEDIUM] C4 — `/health` is unauthenticated and discloses internals
- **File:** `src/rtrade/delivery/api/routes.py:~95-140`
- **Problem:** `/health` returns Postgres `version()`, Redis `used_memory`, and calendar `last_error` strings with no auth (every other route is bearer-protected).
- **Fix:** Split a minimal public liveness (`{"status":"ok"}`) from a detailed authenticated health.

### [MEDIUM] C5 — `X-Forwarded-For` trusted blindly → rate-limit bypass + memory DoS
- **File:** `src/rtrade/delivery/api/routes.py:75-83, 33-58`
- **Problem:** `_client_ip` trusts the first XFF value (spoofable to rotate the S10 auth-failure limit); `_auth_failures` dict grows unbounded (keys never evicted).
- **Fix:** Trust XFF only from the known proxy hop; cap/evict the failure map (LRU/TTL).

### [MEDIUM] C6 — Prompt-injection defenses incomplete
- **File:** `src/rtrade/llm/context_pack.py`, `analyst.py`/`critic.py` prompts, `sanitize.py`
- **Problem:** Only the calendar event **name** is sanitized (rest of event dict merged raw); the `<DATA_TIDAK_TEPERCAYA>` delimiter the system prompts reference is never actually applied in `to_prompt_text()`.
- **Fix:** Sanitize all untrusted fields and wrap untrusted data in the delimiter the prompt expects.

### [MEDIUM] C7 — Device-code OAuth polling loop unbounded
- **File:** `src/rtrade/llm/auth/oauth2.py` (`device_login`)
- **Problem:** `while True` with no `expires_in`/iteration bound → can hang indefinitely.
- **Fix:** Bound by `expires_in` and a max-attempts counter.

### [MEDIUM] C8 — Secrets passed via CLI args
- **File:** `scripts/eval_hallucination.py` (`--api-key`)
- **Problem:** Key visible in `ps`/shell history.
- **Fix:** Read from env / file.

### [MEDIUM] C9 — Log redaction is shallow and misses key names
- **File:** `src/rtrade/core/logging_redact.py`
- **Problem:** Only redacts top-level string values; doesn't recurse nested dict/list; URL pattern matches only `apikey=` (misses `token=`, `api_key=`).
- **Fix:** Recurse structures; broaden key/qs patterns (token, api_key, authorization, refresh_token, secret).

---

## D. Data Integrity & Concurrency

### [HIGH] D1 — Audit hash-chain forks under concurrent writes
- **File:** `src/rtrade/persistence/repositories.py` (`AuditRepo.add`)
- **Problem:** Reads `prev_hash` via `SELECT ... ORDER BY id DESC LIMIT 1` then inserts, with no lock/serialization. Concurrent scheduler tasks read the same parent → chain forks.
- **Impact:** `audit_chain_verify_job` fires false CRITICAL "tampering" alerts; real tampering becomes indistinguishable.
- **Fix:** Serialize chain appends (`pg_advisory_xact_lock` on a chain key, or single-writer queue).

### [HIGH] D2 — OANDA H4/D1 candle alignment violates the UTC grid invariant
- **File:** `src/rtrade/data/oanda_provider.py` (`fetch_ohlcv`)
- **Problem:** No `alignmentTimezone`/`dailyAlignment` sent → OANDA defaults to NY 17:00 alignment, breaking the UTC-epoch candle grid `timeutil.py` assumes.
- **Impact:** Anti-look-ahead cutoff, DST gap detection, and cross-provider MTF alignment all break for XAU/FX.
- **Fix:** Request UTC alignment (`alignmentTimezone=UTC`, `dailyAlignment=0`) or normalize to the UTC grid on ingest.

### [MEDIUM] D3 — Audit-chain verify checks the FIRST 1000 rows, not the last
- **File:** `src/rtrade/scheduler/jobs.py` (`audit_chain_verify_job`)
- **Problem:** `ORDER BY id ASC LIMIT 1000` despite docstring "last 1000" → recent rows never checked once the table exceeds 1000.
- **Fix:** `ORDER BY id DESC LIMIT 1000` then verify in ascending order (with the correct preceding `prev_hash`).

### [MEDIUM] D4 — Candle-gap heuristic hardcodes H1
- **File:** `src/rtrade/data/ingestion.py` (`detect_candle_gaps`)
- **Problem:** Weekend/holiday suppression uses `missing_count <= 72` (assumes H1). Suppresses real multi-week D1 gaps; spams false gaps on M1/M5/M15.
- **Fix:** Compute the threshold from the timeframe duration.

### [LOW] D5 — Several TOCTOU upserts (`get_or_create`, `set_state`, calendar upsert) and an N+1 `EventRepo.upsert_many` (merge-per-row). Use `ON CONFLICT` / batch.

---

## E. Reliability & Robustness

### [MEDIUM] E1 — API creates and disposes an engine/pool on every request
- **File:** `src/rtrade/delivery/api/routes.py` (all handlers)
- **Problem:** `create_engine` + `await engine.dispose()` per request bypasses the loop-aware `_get_engine` singleton in `db.py`.
- **Impact:** Connection churn / pool exhaustion under load.
- **Fix:** Use the shared engine/session-factory (FastAPI dependency).

### [MEDIUM] E2 — `hmm_train_job` blocks the event loop
- **File:** `src/rtrade/scheduler/jobs.py` (`hmm_train_job`)
- **Problem:** CPU-bound `compute_indicators` + `detector.train` run synchronously while holding a DB session → stalls concurrent jobs.
- **Fix:** `run_in_executor`; release the session during compute.

### [MEDIUM] E3 — Alert Markdown injection drops alerts
- **File:** `src/rtrade/monitoring/alerts.py` (`_send_telegram`, `_format_alert`), provider/error strings
- **Problem:** `parse_mode="Markdown"` with unescaped dynamic content (`error[:200]` in backticks, provider names) → Telegram 400 → alert silently dropped.
- **Fix:** Escape dynamic content (MarkdownV2) or send `parse_mode=None`.

### [MEDIUM] E4 — Backfill pagination hardcodes H1/H4
- **File:** `src/rtrade/cli/backfill.py:~120`
- **Problem:** Cursor advances by `1h` (H1) else `4h` × 499 regardless of TF. D1 re-fetches overlapping windows; M5/M15 skip data.
- **Fix:** Advance by `timeframe_duration × batch_size`.

### [MEDIUM] E5 — `composite_market.fetch_spread` has no per-leg try/except
- **File:** `src/rtrade/data/composite_market.py`
- **Problem:** An error in one leg aborts spread lookup instead of failing over.
- **Fix:** Guard each leg; fail over on error.

### [LOW] E6 — Healthcheck DB/Redis connects lack timeouts; `backup_db.sh` lacks `set -o pipefail`; `setup_vps.sh` uses `curl | sh` as root.

---

## F. Numerical / Indicator Correctness

### [MEDIUM] F1 — VWAP is not daily-anchored
- **File:** `src/rtrade/indicators/engine.py:119-126`
- **Problem:** Window-cumulative over the whole 500-bar frame, no per-day reset, despite "rolling daily VWAP" comment. The value fed to the LLM context pack is not a daily mean.
- **Fix:** Reset the VWAP accumulation per UTC day.

### [MEDIUM] F2 — `indicators.compute()` mutates the caller's DataFrame
- **File:** `src/rtrade/indicators/engine.py:73-141`
- **Problem:** In-place dtype coercion + ~15 added columns despite a "pure, no side effects" contract (callers currently avoid harm by reassigning).
- **Fix:** Copy at entry; return a new frame.

### [LOW] F3 — `round_to_tick` under-rounds non-decade tick sizes (e.g. 0.25) — `signals/levels.py:19-30`.
### [LOW] F4 — Volume SMA20 includes the trigger bar (biases ratio low; inconsistent with `edge_quality`) — `signals/confluence.py:178-188`.
### [LOW] F5 — "Nearest level" actually picks the first by ascending price — `signals/confluence.py:119-141`.
### [LOW] F6 — S/R clustering is order/path dependent — `indicators/structure.py:120-150`.
### [LOW] F7 — Strict swing uniqueness drops double tops/bottoms (liquidity pools) — `indicators/structure.py:64-90`.

> Verified clean (bounding false positives): FVG/gap boundaries symmetric; SMC market-structure & liquidity-sweep use confirmed swings (no look-ahead); RSI bands mirror-symmetric; H4→H1 MTF anchor uses the latest *closed* higher-TF bar (no leak); primary `generate_signals` path is causal and fills at `bar_index+1`; papertrack live P&L exit priority is correctly SL-first pessimistic; Fernet encryption uses no IV reuse/weak modes; UTC epoch expiry with 120 s skew is correct; cascade is non-recursive and terminating.

---

## G. Dormant / Lower-priority modules
- **`ml/meta_label.py`:** `prepare_labels` never carries `outcome_r` → expectancy gate metrics ≈0 (meaningless); when present they're in-sample on the refit-on-all-data model (look-ahead). Module is dormant but is a gating metric — fix before relying on it.
- **`ml/similar.py`:** hour-of-day used as a linear Euclidean feature (cyclic encoding needed).
- **`regime/hmm.py`:** label-switching mitigated by `_map_states`; degenerate `n_states<3` collapses crisis mapping (default 3 safe).

---

## Summary counts

| Severity | Count | Domains |
|----------|-------|---------|
| Critical | 0 | — |
| High | 13 | Backtest gates (A1–A7), News fail-open (B1), Budget cap (B2), Security (C1–C3), Data integrity (D1–D2) |
| Medium | ~22 | Risk sizing/Kelly, guardrails fail-open, security C4–C9, concurrency D3–D4, reliability E1–E5, numerical F1–F2 |
| Low | ~12 | metrics edge cases, indicator nuances, infra hygiene |

**Headline:** the code passes lint/type/tests and the *live* signal path is largely sound (causal indicators, pessimistic paper-track exits, UTC discipline, encrypted creds). The real risks cluster in **(1) the backtest/validation gate being too permissive** (A1–A7 — it can green-light an overfit/under-costed strategy), **(2) two fail-open safety paths** (news blackout B1, daily budget B2), and **(3) a few security defaults/leaks** (C1–C3). Remediation plan: `docs/superpowers/plans/2026-06-20-audit-remediation.md`.
