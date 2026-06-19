# ADR-A10 — FinceptTerminal adalah Referensi Konseptual Saja

- **Status:** Accepted
- **Date:** 2026-06-19
- **Decision owner:** Robil Trade product + engineering
- **Related:** PRD §1.2, G-21, NFR-LEG-02, NFR-MAINT-07, NFR-CI-04

## Context
FinceptTerminal berlisensi ganda **AGPL-3.0 + restrictive Fincept Commercial License**,
dengan stated liquidated damages USD 50,000/org/yr untuk penggunaan tidak sah.
Robil Trade adalah produk komersial. Bahkan AGPL personal-use terms tidak menutup
penggunaan komersial ini.

## Decision
FinceptTerminal adalah **conceptual reference ONLY**.
- **DILARANG** menyalin source, snippet, file, data, data-file, atau structure
  FinceptTerminal — termasuk fork yang mengganti API/data-source.
- Setiap ide yang diadopsi **WAJIB** di-re-implement independently dari public
  papers/specs, ATAU dibangun langsung di atas library permissive
  (`ta`/`pandas_ta` MIT, `ccxt` MIT, `quantstats` MIT, `empyrical` Apache-2.0,
  `river` BSD-3, `litellm` MIT, `alternative.me` — per ToS-nya masing-masing).
- Konsep yang umum (token bucket, RSI, walk-forward, fallback chain, DSR/PBO
  dari López de Prado) adalah public knowledge — aman re-implement dari spec netral.

## Consequences
- Code-review checklist WAJIB menyertakan: "✅ Tidak menyalin/adaptasi FinceptTerminal source/snippet/data/structure."
- CI licensing guard (bila ada) WAJIB hijau — grep provenance string Fincept gagal-kan build.

## Alternatives considered (rejected)
- (a) Port modul Fincept spesifik — rejected: contamination liability.
- (b) Hindari seluruh konsep Fincept — unnecessary: re-implementasi independen legal & cukup.
