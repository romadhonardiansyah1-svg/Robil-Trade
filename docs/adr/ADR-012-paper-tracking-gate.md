# ADR-012 — Paper-tracking wajib sebelum sinyal dianggap production

**Status:** ACCEPTED (2026-06-11)

## Decision
Setiap sinyal yang lolos guardrail dicatat sebagai virtual trade (limit fill / SL / TP / expired dievaluasi dari candle berikutnya). Gate terakhir: 4–8 minggu kalibrasi live-paper (P4-T6) sebelum user disarankan memakai sinyal dengan uang nyata.

## Consequences
Tabel `signals.outcome_r` + agregat kalibrasi per bucket confidence; expectancy guard GR-13 menonaktifkan strategi dengan rolling expectancy < 0.
