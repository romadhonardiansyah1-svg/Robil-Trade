# ADR-001 — Produk = asisten sinyal, bukan bot eksekusi

**Status:** ACCEPTED (2026-06-11)

## Context
Dua laporan riset berbeda arah: `deep_research_report.md` mengarah ke bot eksekusi otomatis, `DEEP_RESEARCH_TRADING_AI.md` (final, lebih baru) menetapkan asisten sinyal dengan eksekusi manual oleh user.

## Decision
Sistem HANYA menghasilkan sinyal (entry LIMIT / SL / TP / saran sizing). Tidak ada eksekusi order.

## Consequences
- Tidak ada modul order management.
- Semua kredensial pasar bersifat read-only / public data — tidak pernah ada API key exchange dengan permission trade/withdraw.
- Latensi bukan constraint kritis (timeframe 1H/4H).
