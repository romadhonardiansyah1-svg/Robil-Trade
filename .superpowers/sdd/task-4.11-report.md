# Task 4.11 — Keyed-HMAC Model Integrity, Fail Closed (C3)

## Why the unkeyed SHA-256 sidecar was useless

`model_io.py` wrote a plain `SHA-256` of the model file to a `.sha256` sidecar
and verified it before `joblib.load` (pickle → arbitrary code execution). The
sidecar sat next to the model with **no secret involved**. An attacker who can
overwrite the model file (compromised host, supply-chain, shared volume) simply:

1. replaces `hmm_XAUUSD.joblib` with a malicious pickle, then
2. recomputes `sha256(malicious_file)` and rewrites `hmm_XAUUSD.joblib.sha256`.

On the next load the recomputed hash equals the attacker-written sidecar, the
check passes, and the malicious pickle is deserialized → RCE. A hash anyone can
recompute authenticates nothing; it only detects accidental corruption, not
tampering. Integrity against an adversary requires a **keyed MAC** whose secret
the attacker does not possess.

## Keyed-HMAC + fail-closed design

- Sidecar is now `HMAC-SHA256(key, file_bytes)` written to a `.hmac` suffix.
  The old `.sha256` path was removed entirely.
- The secret key is **never stored beside the model**. It comes from an explicit
  argument (tests / threaded call sites) or, when omitted, from
  `Secrets.model_hmac_key` (env `MODEL_HMAC_KEY` / `.env`).
- Verification uses `hmac.compare_digest` (constant-time) and `joblib.load` runs
  **only** after a verified match.
- Controls **fail CLOSED** — every untrusted state refuses to load:
  - no key (save or load) → `RuntimeError` (`MODEL_HMAC_KEY ... fail closed`)
  - no `.hmac` sidecar → `RuntimeError` (`tanpa sidecar HMAC`)
  - HMAC mismatch → `RuntimeError` (`integritas ... GAGAL ... kemungkinan tamper`)
  No plain-hash fallback exists, so a missing key can never silently downgrade
  protection.

## MODEL_HMAC_KEY wiring + call sites

- `core/config.py` `Secrets`: added `model_hmac_key: str = ""` (env
  `MODEL_HMAC_KEY`, pydantic-settings case-insensitive). Empty default → no
  committed secret; resolves to "no key" → fail closed.
- `.env.example` and `.env.prod.example`: documented `MODEL_HMAC_KEY` as REQUIRED
  for ML model save/load, generate with `openssl rand -hex 32`, no committed value.
- `scheduler/jobs.py` `hmm_train_job`: `save_model(..., hmac_key=cfg.secrets.model_hmac_key)`
  — `cfg` is already loaded there, so the key is threaded explicitly.
- `pipeline/scan.py` `_hmm_shadow_classify` (load path): `cfg` is not in scope and
  threading it would ripple through several call signatures, so `load_model`
  resolves the key internally from `Secrets`/env (the documented fallback). Still
  fail-closed.
- `ml/meta_label.py` save/load: left on the internal-resolution path (no disk test
  exercises it); inherits the same fail-closed behavior.

## Migration impact

Existing models protected only by `.sha256` become **unloadable** under the new
scheme (no `.hmac` sidecar → refuse). This is correct: those files were never
actually tamper-protected. The next `hmm_train_job` re-saves each model with a
keyed-HMAC `.hmac` sidecar. No backward-compat weakening, no plain-hash path kept.

## The threat test (b) — C3 core proof

`test_threat_attacker_rewrites_model_and_sidecar`: save a benign model with key
`K`; the attacker overwrites the model with a malicious pickle AND rewrites the
sidecar with a freshly computed **plain `sha256`** of the malicious file (exactly
what the old scheme accepted). Loading with the real key `K` **raises** because
`plain_sha256(file) != HMAC(K, file)` — the attacker cannot forge the MAC without
`K`. This is the direct demonstration that the previous design was bypassable and
the new one is not.

## Existing tests changed + why

`tests/unit/test_model_io.py` was migrated to the HMAC scheme (assertions
preserved/strengthened, not deleted):

- `test_save_load_round_trip` — now passes `hmac_key=_KEY` on save and load;
  still asserts the object round-trips.
- `test_sidecar_created` → `test_hmac_sidecar_created_not_sha256` — asserts the
  `.hmac` sidecar exists AND the legacy `.sha256` is absent (proves removal).
- `test_load_without_sidecar_raises` → `test_load_without_sidecar_refuses` —
  message updated to `tanpa sidecar HMAC`; still asserts refusal.
- `test_tampered_model_rejected` — unchanged intent (overwrite bytes only, stale
  sidecar → `integritas ... GAGAL`).
- `test_tampered_sidecar_rejected` — garbage in sidecar → integrity failure
  (now against the `.hmac` sidecar).
- Added `test_save_without_key_refuses`, `test_load_without_key_refuses`
  (fail-closed on missing key) and the threat test (b) above.

## Verification

- RED: all 8 tests failed first (`save_model()/load_model() got an unexpected
  keyword argument 'hmac_key'`) — new API absent, as expected.
- GREEN: `pytest -q tests/unit/test_model_io.py` → 8 passed.
- Full suite: `pytest -q` → all passed (7 pre-existing skips, 1 unrelated
  Starlette deprecation warning).
- `ruff check src tests` → All checks passed.
- `mypy src` (strict) → Success: no issues found in 129 source files.

## Commit

`fix(security): keyed-HMAC model integrity, fail closed (C3)` — hash: `1e80d8650e386627ad829889fa5d7435e2f620dd`

## Concerns

- `pipeline/scan.py` and `ml/meta_label.py` rely on internal key resolution from
  `Secrets`/env rather than an explicitly threaded key. Functionally fail-closed,
  but the dependency is implicit; a future refactor could thread `cfg` for
  symmetry with the save path.
- `Secrets()` is instantiated inside `_resolve_key` on the fallback path; it reads
  `.env` each call. Acceptable for model load frequency, but if load becomes
  hot it should be cached/passed in.
- Operationally: deployments MUST set `MODEL_HMAC_KEY` before training or loading,
  or every model op fails closed (by design). Rotating the key invalidates all
  existing `.hmac` sidecars until models are re-saved.
