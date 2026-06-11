# ADR-007 — Backtester: vectorbt (sweep) + event-loop kustom (angka resmi)

**Status:** ACCEPTED (2026-06-11)

## Decision
vectorbt untuk eksplorasi kasar ribuan kombinasi parameter; backtester kustom bar-by-bar (fill di bar berikutnya, model biaya konservatif, SL-first pada bar ambigu) sebagai angka resmi; walk-forward harness sendiri + DSR + PBO.

## Consequences
Dua jalur harus menghasilkan arah kesimpulan sama; jika tidak → investigasi bug sebelum lanjut.
