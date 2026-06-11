# ADR-009 — Secrets: .env (600) + sops/age, BUKAN HashiCorp Vault (MVP)

**Status:** ACCEPTED (2026-06-11)

## Decision
`.env` di dev & VPS (chmod 600), dienkripsi saat transit dengan sops+age. Vault tidak dipakai di MVP — overhead operasional tidak sebanding untuk single-VPS single-user.

## Consequences
Rotasi key manual per 90 hari via runbook. Konfig loader menolak OAuth token konsumen (PLAN §14.2) pada level kode.
