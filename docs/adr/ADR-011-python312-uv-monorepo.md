# ADR-011 — Python 3.12 + uv + monorepo tunggal

**Status:** ACCEPTED (2026-06-11)

## Decision
Python 3.12 (pin <3.13), `uv` sebagai package manager (uv.lock di-commit), satu repo `robil-trade/` src-layout, mypy --strict pada src/, ruff dengan rule DTZ (paksa timezone-aware datetime) dan T20 (larang print).
