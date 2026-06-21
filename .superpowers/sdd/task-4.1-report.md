# Task 4.1 — API routes audit remediation (E1, C4, C5)

Branch: `fix/audit-remediation`
Files touched:
- `src/rtrade/delivery/api/routes.py` (E1, C4, C5)
- `src/rtrade/delivery/api/app.py` (E1 — lifespan disposal hook)
- `config/Caddyfile` (C4 — public-route comment/clarification)
- `tests/unit/test_api_audit_remediation.py` (new — RED→GREEN tests)

Methodology: strict TDD (RED → GREEN), ruff + mypy(strict) clean, one commit.

---

## E1 — Per-request engine/pool churn → shared loop-aware engine

### Defect
Every handler did `engine = create_engine(...)` then `await engine.dispose()` in a
`finally:`, bypassing the loop-aware `db._get_engine` singleton. Under load this
churns connections / exhausts the pool (a fresh engine + pool created and torn
down on every request).

### Fix
- New module-level accessor in `routes.py`:
  ```python
  def _get_session_factory(cfg: AppConfig) -> async_sessionmaker[AsyncSession]:
      engine = _get_engine(cfg.secrets.database_url)   # loop-aware, cached per (loop,url)
      return create_session_factory(engine)
  ```
  This mirrors how `pipeline/scan.py`, `scheduler/jobs.py`, `cli/*`, and
  `delivery/telegram_bot.py` obtain their factory (`create_session_factory(_get_engine(url))`).
- Replaced the `create_engine(...)` + `try/finally: await engine.dispose()` block
  in **every** handler (`/signals`, `/signals/{id}`, `/calibration`, `/metrics`,
  `/analytics/exits`, `/analytics/excursion`, `/analytics/failures`, and the new
  `/health/detail`) with `factory = _get_session_factory(cfg)` + a plain
  `async with factory() as session:`.
- Import changed from `create_engine` → `_get_engine`. `create_engine` is no
  longer referenced in the request path.

### Disposal policy
The shared engine is process-scoped and loop-aware (one engine per running event
loop), so it must live for the process lifetime — **never** disposed inside a
request handler. Disposal now happens exactly once, on app shutdown, via a new
FastAPI **lifespan** in `app.py` that calls `db.shutdown_process_resources()`
(the same graceful-shutdown routine the worker uses). `TestClient` only triggers
lifespan when used as a context manager, so existing tests (which don't) are
unaffected.

---

## C4 — Unauthenticated `/health` leaked internals

### Defect
Public `/health` returned `HealthChecker.run_all().to_dict()` — including Postgres
`version()`, Redis `used_memory_human`, per-check `details`/`message`, **and**
calendar-source `last_error` strings — all with no bearer token.

### Fix — split into public liveness + authenticated telemetry
- **PUBLIC** `GET /health` (no auth): minimal liveness. Returns **only**
  `{"status": "ok" | "degraded"}` (`HEALTHY` → `ok`, anything else → `degraded`).
  No versions, memory, per-check details, or calendar errors. Return type
  narrowed to `dict[str, str]`.
- **AUTHENTICATED** `GET /health/detail` (behind `_require_bearer`): the full
  `to_dict()` telemetry + `calendar_sources` (incl. `last_error`). Uses the shared
  session factory (E1). 401 without a valid bearer.

### Caddyfile note
`config/Caddyfile` already exposes only `handle /health` publicly; in Caddy this
is an **exact** path match, so `/health/detail` is NOT covered and automatically
falls through to the bearer-gated catch-all `handle` (401 without token). Added a
clarifying comment documenting that only `/health` (exact) is public. No routing
change was required — the split is consistent with existing Caddy behavior.

---

## C5 — Blindly-trusted XFF + unbounded auth-failure map

### Client-IP trust model
Behind a single known reverse proxy (Caddy), Caddy **appends** the real peer IP it
saw to `X-Forwarded-For`, so the **rightmost** hop is set by the trusted proxy and
is not client-controllable; the leftmost entries are attacker-supplied. The old
code used `forwarded.split(",")[0]` (leftmost) — a client could rotate that value
to mint a fresh rate-limit key and bypass the S10 per-IP auth-failure limiter.

`_client_ip` now takes the **rightmost** non-empty XFF hop, falling back to
`request.client.host` (the direct peer) when no XFF is present. The rate-limit key
is therefore not attacker-controlled.

### Bounded `_auth_failures`
- Changed `_auth_failures` from `defaultdict(list)` to an `OrderedDict` (LRU order).
- `_prune_auth_failures(now)` runs at the top of every `_require_bearer` call and
  deletes any key whose timestamps have *all* aged out of the window — so keys for
  one-off failing IPs no longer accumulate forever.
- `_record_auth_failure(client_ip, now)` appends the timestamp, marks the key
  most-recently-used (`move_to_end`), and enforces a hard cap
  `_AUTH_FAIL_MAX_KEYS = 4096` by evicting the oldest key
  (`popitem(last=False)`) — bounding memory under a distributed/spoofed-IP flood.
- Per-key window pruning (the original behavior) is preserved.

---

## Tests (`tests/unit/test_api_audit_remediation.py`)

- **E1** `test_handlers_reuse_shared_engine_and_never_dispose` — patches
  `routes._get_engine` to a shared mock engine + fake factory; two `/signals`
  requests both 200, `_get_engine` called, and `engine.dispose` **never** called
  in the request path.
- **C4** `test_public_health_returns_only_status_no_internals` — body keys ==
  `{"status"}`, status ∈ {ok, degraded}, and response text contains none of
  `version`/`PostgreSQL`/`used_memory`/`calendar_sources`/`last_error`.
- **C4** `test_public_health_degraded_when_not_healthy` — UNHEALTHY → `{"status":"degraded"}`.
- **C4** `test_health_detail_requires_bearer` — 401 without token.
- **C4** `test_health_detail_returns_full_telemetry_with_bearer` — 200 with
  `checks` + `calendar_sources` when authenticated.
- **C5** `test_client_ip_uses_trusted_rightmost_hop` — `"1.1.1.1, 2.2.2.2"` → `2.2.2.2`.
- **C5** `test_spoofed_leftmost_xff_cannot_change_ratelimit_key` — differing
  leftmost values, same rightmost → identical key.
- **C5** `test_auth_failures_map_is_bounded` — `_AUTH_FAIL_MAX_KEYS + 100` distinct
  failing IPs → `len(_auth_failures) <= _AUTH_FAIL_MAX_KEYS`.
- **C5** `test_auth_failures_evicts_expired_keys` — a stale key is evicted on the
  next `_require_bearer` call.

### RED (before implementation)
`pytest -q tests/unit/test_api_audit_remediation.py` → **9 failed** for the
expected reasons:
- E1: `routes` has no `_get_engine` attribute (shared accessor not wired).
- C4: public `/health` body contained `checks`/`calendar_sources`; `/health/detail` → 404.
- C5: `_client_ip` returned leftmost (`1.1.1.1` / `9.9.9.9` ≠ `8.8.8.8`); no
  `_AUTH_FAIL_MAX_KEYS`; stale key not evicted.

### GREEN (after implementation)
- `pytest -q tests/unit/test_api_audit_remediation.py` → **9 passed**.
- Existing API tests (`test_api_security.py`, `test_api_app.py`,
  `test_auth_rate_limit.py`) → **all passed** (no regressions; the existing S10
  rate-limit test still goes 10×403 → 429).
- **Full suite** `pytest -q` → **all passed, 7 skipped** (pre-existing skips).
- `ruff check src tests` → All checks passed!
- `mypy src` (strict) → Success: no issues found in 129 source files.

---

## Commit
`fix(api): shared engine + public/auth health split + trusted client-IP & bounded auth map (E1,C4,C5)`
Single commit on branch `fix/audit-remediation` (HEAD). Pre-commit hooks
(ruff check + ruff format) pass.

## Concerns / follow-ups
- Public `/health` still runs the full `HealthChecker.run_all()` (DB + Redis
  probes) unauthenticated to derive `ok`/`degraded`. It no longer leaks any
  strings, but it does perform unauth backend work on each hit. If liveness load
  becomes a concern, consider a pure process-up `{"status":"ok"}` liveness and
  move the dependency probes entirely under `/health/detail`. Left as-is to keep
  the `ok|degraded` semantics the task specified.
- `_prune_auth_failures` is an O(tracked-keys) scan per auth call; bounded by
  `_AUTH_FAIL_MAX_KEYS` (4096) so it's cheap. The 60s window + LRU cap make the
  map self-trimming under normal load.
- The XFF trust model assumes exactly one trusted proxy (Caddy) appends the peer.
  If additional proxies are ever chained in front, the trusted-hop index must be
  revisited.
