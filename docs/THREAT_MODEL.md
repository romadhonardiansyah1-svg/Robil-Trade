# Threat Model & Security Runbook — Robil Trade

> Version: 1.0 • Updated: 2026-06-12

## 1. System Overview

Robil Trade is a **signal-only** trading system — no execution, no custody.
LLM augments deterministic analysis; it may NOT modify numbers (GR-10).

### Components

| Component | Role | Exposure |
|-----------|------|----------|
| Scheduler (APScheduler) | Cron-driven scan pipeline | Internal only |
| API (FastAPI) | Signal & analytics CRUD | Behind Caddy (TLS) |
| Telegram Bot | Push signals to user | Outbound only |
| PostgreSQL + TimescaleDB | Persistent data | Internal only |
| Redis | Cache, rate limits | Internal only |
| Caddy | TLS termination, auth proxy | Internet-facing |

---

## 2. Threat Matrix

### Tier 1 — Secrets & Access

| ID | Threat | Control | Status |
|----|--------|---------|--------|
| T1 | Timing attack on Bearer token | `hmac.compare_digest` in `_require_bearer` (S1) | ✅ |
| T2 | OpenAPI/docs leak in prod | Conditional `docs_url=None` when `ENV=prod` (S1) | ✅ |
| T3 | Missing auth on endpoints | All non-`/health` routes require Bearer (S1) | ✅ |
| T4 | Plaintext tokens in prod | `save_token` → `RuntimeError` without key (S3) | ✅ |
| T5 | Security headers missing | `_SecurityHeaders` middleware: nosniff, DENY, no-referrer, no-store (S1) | ✅ |

### Tier 2 — LLM Defense

| ID | Threat | Control | Status |
|----|--------|---------|--------|
| T6 | Prompt injection via calendar event | `sanitize_untrusted` + `DATA_TIDAK_TEPERCAYA` delimiter (S4) | ✅ |
| T7 | LLM mutates trading numbers | GR-10 bit-perfect check + adversarial test (S5) | ✅ |
| T8 | LLM injects extra JSON keys | `ConfigDict(extra="forbid")` on schemas (S5) | ✅ |
| T9 | Confidence manipulation | Clamped ±0.15 from base (pipeline.compute_confidence) (S5) | ✅ |

### Tier 3 — Supply Chain & Container

| ID | Threat | Control | Status |
|----|--------|---------|--------|
| T10 | Floating Docker tags | Pinned versions: `caddy:2.9`, `redis:7.4`, `postgres:16.8`, `timescaledb:2.17.2` (S6) | ✅ |
| T11 | Container escape / privilege escalation | `read_only`, `cap_drop: ALL`, `no-new-privileges` (S7) | ✅ |
| T12 | NaN/Inf poisoned candle data | `isfinite` + positive check in `Candle.__post_init__` (S8) | ✅ |

### Tier 4 — Detection & Integrity

| ID | Threat | Control | Status |
|----|--------|---------|--------|
| T13 | Audit log tampering | SHA-256 hash chain on `SignalAudit.detail._chain` (S9) | ✅ |
| T14 | Auth brute force | Per-IP rate limit: 10 failures/60s → 429 (S10) | ✅ |
| T15 | Guardrail regression | `run_guardrail_selftest()` at scheduler startup (S11) | ✅ |
| T16 | Pickle RCE via model load | `model_io.load_model` with SHA-256 sidecar verification (S13) | ✅ |
| T17 | Secret leakage in logs | `redact_processor` strips API keys, Bearer tokens, passwords (S2) | ✅ |

---

## 3. Security Runbook

### 3.1 Incident: Suspicious Auth Activity

```
1. Check Telegram alert for "too many auth failures"
2. Inspect Caddy access logs: grep for source IP
3. If brute-force confirmed:
   a. Block IP at firewall/Caddy layer
   b. Rotate API_AUTH_TOKEN
   c. Verify no unauthorized signals were published
```

### 3.2 Incident: Audit Chain Break

```
1. Run: python -c "from rtrade.persistence.audit_chain import verify_chain; ..."
2. If verify_chain returns (False, idx):
   a. STOP the scheduler immediately
   b. Export raw SignalAudit table for forensic analysis
   c. Identify tampered row at index idx
   d. Check DB access logs for unauthorized modifications
```

### 3.3 Incident: Model Integrity Failure

```
1. Error log: "integritas model ... GAGAL"
2. DO NOT manually re-run joblib.load
3. Delete the tampered .joblib file
4. Retrain from scratch: scheduler will recreate on next Sunday 02:00 UTC
5. Investigate how the file was modified (host access audit)
```

### 3.4 Incident: Prompt Injection Detected

```
1. Log warning: "prompt injection detected in calendar event"
2. Review the calendar data provider for poisoned entries
3. The injected text is already sanitized (replaced with [REDACTED:suspicious])
4. Check if the data provider API has been compromised
```

### 3.5 Key Rotation

```bash
# Generate new Fernet key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Rotate token store
python -c "from rtrade.llm.auth.token_store import rotate_key; rotate_key('OLD_KEY', 'NEW_KEY')"

# Update .env with new RTRADE_TOKEN_KEY
# Restart services
```

### 3.6 Guardrail Selftest Failure

```
1. Scheduler refuses to start with "guardrail selftest FAILED"
2. Review the listed problems — guardrails may have been weakened by code change
3. DO NOT bypass the selftest — fix the underlying issue
4. Re-deploy and verify selftest passes
```

---

## 4. Residual Risks

| Risk | Severity | Mitigation Plan |
|------|----------|-----------------|
| Redis not authenticated | Low | Internal network only; add `requirepass` in v2 |
| No WAF on Caddy | Medium | Add Caddy rate-limit plugin or Cloudflare in front |
| Single-node DB | Medium | Daily pg_dump backup + offsite copy |
| No automated CVE scanning | Medium | Add `trivy` in CI pipeline |

---

## 5. Review Schedule

- **Monthly**: Review access logs, audit chain integrity
- **Quarterly**: Update pinned Docker digests, review this threat model
- **On incident**: Update runbook with lessons learned
