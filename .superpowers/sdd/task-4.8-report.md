# Task 4.8 — Security remediation (C6, C7, C9)

Branch: `fix/audit-remediation` · Python 3.12 · TDD (RED → GREEN) · one commit.

## Summary

Three security defects fixed with test-first discipline. All affected-module tests,
the full suite, `ruff check src tests`, and `mypy src` (strict) are clean.

---

## DEFECT C6 — prompt-injection defenses incomplete

**Files:** `src/rtrade/llm/context_pack.py`, `src/rtrade/llm/sanitize.py` (reused).

**Problem:** Only the calendar event `event` (name) was sanitized; every other
string in each event dict was merged raw via `{**evt, ...}`. The
`<DATA_TIDAK_TEPERCAYA>` untrusted-data delimiter that `analyst_system.md` and
`critic_system.md` instruct the model to honor was NEVER applied in
`to_prompt_text()`.

**Fix:**
- In `build_context_pack`, every untrusted calendar event is now sanitized
  field-by-field: each **string** value is passed through `sanitize_untrusted`
  (the same helper already used for the name); non-string (numeric) values pass
  through untouched. `contains_injection` is checked across **all** string
  fields (not just the name) and flips the `_injection_detected` marker, logging
  the offending field name + truncated value.
- `to_prompt_text()` now splits the pack: trusted/numeric data is rendered as
  JSON as before, and the untrusted block (`calendar_next_72h`) is fenced inside
  the **exact delimiter the system prompts reference**, verbatim:
  `<DATA_TIDAK_TEPERCAYA>` ... `</DATA_TIDAK_TEPERCAYA>`.
- Trusted/numeric field formatting (indicators, candidate, structure, regime,
  derivatives, recent_summary) is unchanged and stays OUTSIDE the delimiter.

**Fields now sanitized:** every string value of each calendar event (e.g.
`event`, `detail`/`title`, `currency`, and any other free-text/string field),
not just `event`.

**Tests (`tests/unit/test_context_pack_fencing.py`, new — replaces the orphaned
`test_context_pack_fencing.pyc` that had no source):**
- `test_injection_in_non_name_field_is_sanitized` — injection in `detail` →
  `[REDACTED:suspicious]`; raw injection text absent from `to_prompt_text()`.
- `test_untrusted_block_wrapped_in_delimiter` — both delimiter tags present,
  calendar data sits between them.
- `test_trusted_numeric_fields_outside_delimiter` — `entry_limit`/`2000.0`
  appear before the opening delimiter.

---

## DEFECT C7 — device-code OAuth polling loop unbounded

**File:** `src/rtrade/llm/auth/oauth2.py`, `device_login`.

**Problem:** `while True:` with no `expires_in`/iteration bound → could hang
indefinitely if the user never authorizes.

**Fix (deadline + cap design):**
- Deadline computed once before the loop from the device-init `expires_in`
  (default 900s if absent), tz-aware UTC:
  `deadline = utcnow() + timedelta(seconds=expires_in)` using the project
  `rtrade.core.timeutil.utcnow` helper.
- On each iteration the loop checks `utcnow() >= deadline` → raises a clear
  `RuntimeError` (timeout, mentions `expires_in` terlampaui).
- Second safety net: a hard max-iteration cap
  `_DEVICE_POLL_MAX_ITERATIONS = 1000`; exceeding it raises `RuntimeError`
  (covers `interval=0`/clock-stall edge cases so the loop can never spin
  forever).
- Existing `interval` / `slow_down` backoff and the C2 no-token-logging
  behavior are preserved untouched.

**Tests (`tests/unit/test_oauth2.py`, added `TestDeviceLoginBounded`):**
- `test_device_login_times_out_when_never_authorized` — RFC 8628 init with
  `expires_in=1`, `interval=0`; poll always returns
  `{"error": "authorization_pending"}` → raises `RuntimeError` matching
  timeout/expired/max, and `poll_route.call_count` is bounded (≥1, <100000),
  proving it does not loop forever. (Pre-fix this test hung — confirming RED.)

---

## DEFECT C9 — log redaction shallow, misses keys

**File:** `src/rtrade/core/logging_redact.py`.

**Problem:** Only top-level string values were processed; nested dict/list were
ignored. URL query-string regex matched only `apikey=` (missed `token=`,
`api_key=`, `access_token=`, `refresh_token=`).

**Fix:**
- **Recursion:** new `_redact_value(key, value)` recurses into `MutableMapping`
  (dict) and `list` structures. Sensitive **keys** redact their whole value
  regardless of type; strings get value-pattern redaction; list elements are
  recursed with empty key context.
- **Key set** (case-insensitive) now: `api_key`, `api-key`, `apikey`, `token`,
  `authorization`, `refresh_token`, `access_token`, `secret`, `password`
  (existing coverage kept; `apikey` added explicitly).
- **URL regex** broadened to one alternation covering `token=`, `api_key=`,
  `apikey=`, `access_token=`, `refresh_token=` (replacing the `apikey=`-only
  pattern). Bearer / `sk-` / `AIza` patterns retained. Non-sensitive query
  content (e.g. `keep=ok`) preserved.
- structlog processor signature/contract `(logger, name, event_dict) ->
  event_dict` unchanged.

**Tests (`tests/unit/test_logging_redact.py`, added `TestRecursiveRedaction`):**
- `test_nested_dict_secret_redacted` — `{"outer": {"api_key": "SECRET",
  "url": "https://x?token=ABC&api_key=DEF"}}` → none of SECRET/ABC/DEF survive.
- `test_nested_list_secret_redacted` — nested list with `access_token` +
  `refresh_token=` URL redacted.
- `test_non_sensitive_nested_values_preserved` — nested benign values intact.
- `test_url_token_variants_redacted` — all five token variants stripped,
  `keep=ok` preserved.

---

## Verification

| Check | Result |
|-------|--------|
| RED (C6) | 3 fencing tests FAILED (no delimiter, raw injection present) |
| RED (C7) | bounded test HUNG (unbounded `while True` — defect reproduced) |
| RED (C9) | 3 recursion tests FAILED (SECRET/ABC/DEF leaked, token= not matched) |
| GREEN (affected modules) | context_pack + oauth2 + logging_redact + sanitize all PASS; C7 no longer hangs |
| Full suite `.venv\Scripts\pytest.exe -q` | **882 passed, 8 skipped, 1 warning** |
| `.venv\Scripts\ruff.exe check src tests` | All checks passed |
| `.venv\Scripts\mypy.exe src` (strict) | Success: no issues found in 129 source files |

## Concerns / notes
- The one suite warning is a pre-existing Starlette/httpx deprecation unrelated
  to these changes.
- C7 uses `expires_in` default of 900s when the device-init response omits it;
  the max-iteration cap (1000) is the hard backstop for `interval=0` cases.
- `to_prompt_text()` output format changed (delimiter added); no test asserted
  the old exact byte format, and the full suite stays green.

Commit: `fix(security): prompt-injection fencing + bounded device-code loop + recursive log redaction (C6,C7,C9)`
