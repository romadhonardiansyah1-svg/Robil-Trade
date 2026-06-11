# ADR-008 — Persistence: TimescaleDB sejak P0 (bukan SQLite dulu)

**Status:** ACCEPTED (2026-06-11)

## Decision
TimescaleDB (PG16) via docker compose sejak P0; hypertable `candles`; Redis untuk cache & rate-limit. Tidak ada jalur SQLite.

## Consequences
Parity dev/prod penuh; tidak ada migrasi ganda; dev butuh Docker berjalan (integration test auto-skip bila mati).
