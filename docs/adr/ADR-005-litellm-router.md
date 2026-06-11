# ADR-005 — LLM router: LiteLLM Proxy self-host + Redis

**Status:** ACCEPTED (2026-06-11)

## Decision
Semua call LLM lewat LiteLLM Proxy (satu endpoint OpenAI-compatible): fallback, cooldown 429, retry, budget per virtual key, state lintas instance via Redis. Kode aplikasi TIDAK pernah memanggil SDK provider langsung dan hanya tahu alias model (`trading-analyst`, `trading-critic`, `trading-backup`).

## Consequences
Ganti/rotasi provider = edit `config/litellm.yaml`, tanpa menyentuh kode aplikasi.
