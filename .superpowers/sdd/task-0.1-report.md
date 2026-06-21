# Task 0.1 — Require API auth token, remove `changeme` default (audit finding C1)

**Branch:** `fix/audit-remediation`
**Commit:** `3066456d1f499b151294d9e45708f4d0a10d7ca1`
**Status:** DONE

## Summary

Closed audit finding C1: the reverse proxy previously accepted a hard-coded
`Bearer changeme` token whenever `API_AUTH_TOKEN` was unset, silently opening
every non-health route. All controls now fail **closed** — an unset token
yields no usable default at the proxy, and `docker compose` refuses to start.

## Files changed (with rationale)

1. **`config/Caddyfile`**
   - Changed the `@no_auth` matcher from `"Bearer {$API_AUTH_TOKEN:changeme}"`
     to `"Bearer {$API_AUTH_TOKEN}"` (no default).
   - Added a comment documenting that an unset var substitutes to an empty
     string, so the matcher compares against `Bearer ` and no real client token
     can match → every request hits `@no_auth` → 401. Fail closed.

2. **`docker-compose.prod.yml`** (caddy service only)
   - Changed `API_AUTH_TOKEN: ${API_AUTH_TOKEN:-changeme}` to
     `API_AUTH_TOKEN: ${API_AUTH_TOKEN:?API_AUTH_TOKEN must be set}`.
   - The `:?err` form makes `docker compose` abort with the given message when
     the variable is unset or empty, preventing an insecure boot.
   - The `api` service was intentionally **not** changed: it does not pass the
     token via compose interpolation (it loads it from `.env` via `env_file`),
     so adding it there would alter unrelated config. The caddy guard already
     blocks the whole stack from starting when the token is missing.

3. **`.env.prod.example`**
   - Annotated `API_AUTH_TOKEN` as REQUIRED with no default, documented the
     fail-closed behavior, and reminded not to commit a real secret. Left the
     value blank (no placeholder secret).

4. **`tests/unit/test_api_security.py`**
   - Added `TestTokenNotConfigured::test_require_bearer_503_when_token_unset`,
     a focused unit test (parametrized over `""` and `None`) that calls
     `_require_bearer` directly and asserts `HTTPException.status_code == 503`.

## Test added / confirmed

- **Pre-existing:** `test_503_empty_token` already asserted a 503 at the HTTP
  layer for an empty token — left intact, not duplicated.
- **New (this task):** `test_require_bearer_503_when_token_unset` exercises the
  unit `_require_bearer` directly and adds the `None` case (the true "env var
  unset" scenario). This is a confirmation/regression test: `routes.py` already
  raises 503 when `cfg.secrets.api_auth_token` is falsy (per task instructions,
  that production behavior was kept unchanged). The test meaningfully guards the
  branch — if the 503 guard were removed, `hmac.compare_digest(token, None)`
  would raise `TypeError`, not `HTTPException(503)`, so the test would fail.

## Commands run and output

```
.venv\Scripts\pytest.exe -q tests/unit/test_api_security.py
  -> 18 passed, 1 warning (StarletteDeprecationWarning, pre-existing/unrelated)

.venv\Scripts\ruff.exe check src tests
  -> All checks passed!

.venv\Scripts\mypy.exe src
  -> Success: no issues found in 129 source files
```

Pre-commit hooks on commit: ruff check Passed, ruff format Passed, block-.env Skipped.

## Reasoning about the `:?` compose syntax (no Docker available here)

`${VAR:?message}` is standard Docker Compose / POSIX shell parameter
expansion: if `VAR` is unset **or empty**, expansion fails and Compose exits
with an error containing `message`. (`${VAR?message}` would only trigger on
unset, allowing an empty value; the `:` form also rejects empty, which is what
we want for fail-closed behavior.) `docker compose -f docker-compose.prod.yml
config` would therefore error out when `API_AUTH_TOKEN` is missing/empty and
succeed once it is set. This could not be executed here (no Docker in the
environment) but the syntax is correct.

## Concerns

- The Caddy matcher relies on an unset `{$API_AUTH_TOKEN}` resolving to an empty
  string so that `Bearer ` matches nothing usable. In practice the compose `:?`
  guard prevents Caddy from ever starting without the token, so this is a
  defense-in-depth backstop rather than the primary control. Both layers fail
  closed; no live Docker run was possible to integration-test the proxy path.
- The `api` service receives the token via `.env`; `AppConfig`/`_require_bearer`
  enforce the 503 fail-closed path at the application layer (verified by tests).
