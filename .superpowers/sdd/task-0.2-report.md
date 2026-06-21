# Task 0.2 — Stop logging OAuth token bodies (audit finding C2)

**Status:** DONE
**Branch:** `fix/audit-remediation`
**Commit:** `83cd51cfe980575c3a879307f15fa5648ab8dbe7` — `fix(security): never log OAuth token bodies (C2)`

## Summary
The Codex 2-step device-code token-exchange path in `OAuth2Provider.device_login()` emitted the
full token response (containing `access_token` + `refresh_token`) to structlog and embedded the raw
response body into a `RuntimeError`. Fixed both leaks by logging only non-sensitive metadata and
making the error message generic (status + short reason). OAuth behavior is unchanged — callers still
receive the same `StoredToken`.

## Exact lines that leaked before (src/rtrade/llm/auth/oauth2.py, token-exchange path)
1. **structlog leak** — the success/exchange log:
   ```python
   logger.info(
       "token exchange response",
       body=token_body,          # <-- dict with access_token + refresh_token
       status=token_resp.status_code,
   )
   ```
   Why it leaked despite `redact_processor`: that processor only redacts sensitive *top-level key
   names* and *string values*. Here the sensitive data sat in a **nested dict** under the key `body`
   (not a sensitive name, not a string), so the nested `access_token`/`refresh_token` were never
   redacted.
2. **exception-message leak** — the failure branch:
   ```python
   raise RuntimeError(f"token exchange gagal: {token_body}")   # <-- dumps full raw body
   ```

## Change made (rationale)
File: `src/rtrade/llm/auth/oauth2.py` (Codex `authorization_code` exchange branch)
- Logging now emits only non-sensitive metadata: `status`, `scope`, `expires_in`, `token_type`.
  Never the access/refresh token.
- `RuntimeError` is now generic: `f"token exchange gagal (status {status}): {error_code_or_reason}"`
  where the reason is the provider `error` field (an error code like `invalid_grant`), not the body.
- Token construction, `save_token`, and the returned `StoredToken` are untouched → identical behavior
  for callers.

Scope kept tight: only what is *logged/raised* changed. Other branches (`poll response` logs only
`keys=`, the success-path `device code login berhasil` logs only `provider`) were already safe and
were left unchanged.

## Test added (tests/unit/test_oauth2.py → class `TestTokenExchangeNoLeak`)
Drives the real Codex device-code → authorization_code → token-exchange path using `respx` mocks
(device-init with `interval: 0` to avoid sleeping, a poll returning `authorization_code`, then the
`https://auth.openai.com/oauth/token` exchange). Sentinels `SENTINEL_ACCESS` / `SENTINEL_REFRESH`.

How it captures logs: uses `structlog.testing.capture_logs()` — this captures the event dicts exactly
as passed into the logger (before the processor chain), so it proves the *code itself* never hands
token values to structlog, independent of the redaction processor.

- `test_token_exchange_does_not_log_token_values`: asserts the caller still receives the real tokens
  (behavior preserved) **and** no captured log record contains either sentinel.
- `test_failed_exchange_error_message_omits_body`: exchange returns 400 with sentinels in the body;
  asserts the raised `RuntimeError` message contains neither sentinel, and the failed body was not
  logged either.

## Commands + output
RED (before fix):
```
.venv\Scripts\pytest.exe -q tests/unit/test_oauth2.py::TestTokenExchangeNoLeak
FF  -> both fail:
  - "token exchange response" event contained body={'access_token':'SENTINEL_ACCESS', 'refresh_token':'SENTINEL_REFRESH', ...}
  - RuntimeError msg: "token exchange gagal: {... 'leaked_access':'SENTINEL_ACCESS', ...}"
```
GREEN (after fix):
```
.venv\Scripts\pytest.exe -q tests/unit/test_oauth2.py
..........  (10 passed)
```
Lint / types:
```
.venv\Scripts\ruff.exe check src tests   -> All checks passed!
.venv\Scripts\mypy.exe src               -> Success: no issues found in 129 source files
```
Pre-commit hooks on commit: ruff check Passed, ruff format Passed.

## Concerns
- Two sibling error branches in the same method still interpolate the **poll** response into messages:
  `raise RuntimeError(f"device login gagal: {body}")` (lines ~252 and ~259). These execute only on
  error responses where no token is present (the `access_token`/`authorization_code` checks already
  failed), so risk is low, but a non-standard provider could place sensitive data there. Left untouched
  to avoid scope creep beyond finding C2's token-exchange path; recommend a follow-up to genericize
  those two messages as well.
