# ADR-004 — Indikator: pandas-ta, bukan TA-Lib C

**Status:** ACCEPTED (2026-06-11)

## Context
TA-Lib membutuhkan kompilasi C library — gesekan besar di Windows dev + VPS.

## Decision
`pandas-ta` (pure Python). Jika bermasalah dengan numpy/pandas terbaru: pin numpy ke versi kompatibel ATAU pakai fork `pandas-ta-classic` — keputusan dicatat di ADR baru.

## Consequences
Golden test wajib membandingkan nilai RSI/ATR/EMA/ADX dengan nilai referensi terverifikasi manual (PLAN §12.2) untuk mendeteksi perbedaan implementasi smoothing.
