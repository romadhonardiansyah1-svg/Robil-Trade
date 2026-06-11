# ADR-006 — Orkestrasi: pipeline Python sekuensial, bukan LangGraph (P2)

**Status:** ACCEPTED (2026-06-11)

## Decision
Analyst → Critic → Verifier sebagai tiga step sekuensial deterministik. Verifier bahkan BUKAN LLM (number/claim matching deterministik). LangGraph dievaluasi ulang di P3.

## Consequences
Lebih mudah di-test, di-log, di-debug. Pola debat diadopsi dari TradingAgents (polanya saja — klaim performa mereka sudah dibantah riset).
