# Robil Trade — Operations Runbook

Concise operations reference for the deployed VPS stack. For first-time
infrastructure activation see [`RUNBOOK_ACTIVATION.md`](RUNBOOK_ACTIVATION.md).
For incident-specific procedures see [`runbooks/`](runbooks/).

---

## Secrets

- `.env` lives at the project root on the VPS. Lock it down: `chmod 600 .env`,
  owner `rtrade:rtrade`.
- **Never commit `.env`** or any secret. Verify with `git status` (must be clean)
  before any push.
- Rotate the API bearer token (`API_AUTH_TOKEN`): edit `.env`, then
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml restart api`.
- Full key-rotation procedure: [`runbooks/rotate-api-keys.md`](runbooks/rotate-api-keys.md).

## Calendar keys

The calendar fail-CLOSES (GR-07b) so at least one working source must stay
configured. Sources and order are defined in `config/settings.yaml`
(`calendar.sources`): investing (primary, keyless) → nasdaq (secondary) →
static_high_impact (last resort). Endpoint/ToS rationale: [`adr/ADR-A12-investing-calendar-endpoint-tos.md`](adr/ADR-A12-investing-calendar-endpoint-tos.md).

- **Finnhub** (optional paid upgrade): rotate at https://finnhub.io/dashboard,
  update `FINNHUB_API_KEY` in `.env`.
- **Nasdaq Data Link**: rotate at https://data.nasdaq.com/account/profile,
  update `NDAQ_API_KEY` in `.env`.

After changing a key, restart the worker/api so the new value is picked up.

## Caddy TLS / security headers

- Caddy terminates TLS on port 443 with auto-provisioned certificates
  (`config/Caddyfile`, `$DOMAIN`).
- `/health` is public; all other routes require `Authorization: Bearer
  $API_AUTH_TOKEN` (deny-by-default, 401 otherwise).
- Security headers set: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`,
  and the `Server` header is stripped.
- Verify: `curl -sI https://<domain>/health` → confirm the headers are present.

## Backup

- `scripts/backup_db.sh` runs daily at 03:00 UTC in the backup container:
  compressed `pg_dump` to `/backups/rtrade_<timestamp>.sql.gz`, with 30-day
  retention (older backups auto-pruned). The script exits non-zero if the dump
  is suspiciously small (< 1000 bytes) for alerting.
- Verify backups exist: `ls -lh /backups/rtrade_*.sql.gz`.

## Incident runbooks

Incident-specific procedures live in [`runbooks/`](runbooks/):

- [`deploy.md`](runbooks/deploy.md) — deployment procedure
- [`rollback.md`](runbooks/rollback.md) — rolling back a release
- [`rotate-api-keys.md`](runbooks/rotate-api-keys.md) — key rotation
- [`incident-data-gap.md`](runbooks/incident-data-gap.md) — data gaps
- [`incident-hallucination.md`](runbooks/incident-hallucination.md) — LLM hallucination
- [`oauth-provider-onboarding.md`](runbooks/oauth-provider-onboarding.md) — OAuth provider onboarding
