# Code Review Checklist — Robil Trade

Setiap PR WAJIB diverifikasi terhadap checklist ini sebelum merge.

## Legal / License
- [ ] **ADR-A10:** Tidak ada source/snippet/data/structure FinceptTerminal yang disalin atau diadaptasi.
- [ ] Library baru: license permissive (MIT/BSD/Apache) atau terms data-source dihormati; dicatat di ADR/PR.

## Safety invariants (non-negotiable)
- [ ] Signal-only: tidak ada order placement / broker / auto-execution (ADR-001).
- [ ] `calendar.fail_open_when_stale: false` (fail-CLOSE default tidak dilemahkan).
- [ ] Risk floors utuh: GR-03 RR≥1.5, GR-04 SL∈[0.5,3.0]×ATR, GR-05 risk≤2%. Config yang melemahkan gagal load.
- [ ] `llm.enabled: false` di production (hanya staging akhir P2).

## Quality gates
- [ ] `ruff check` + `ruff format --check` hijau.
- [ ] `mypy --strict src/` hijau (no new `type: ignore` tanpa justifikasi).
- [ ] Tes baru: `freezegun` untuk waktu, `respx` untuk HTTP, no live network.
- [ ] Coverage floor per-package terpenuhi (lihat IMPLEMENTATION_PLAN_v2.md §9.3).
