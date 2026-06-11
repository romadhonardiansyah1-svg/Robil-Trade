# Robil Trade

**Asisten sinyal trading presisi ber-otak AI** — XAUUSD · Forex · Crypto.
Menghasilkan sinyal terstruktur (entry LIMIT / SL / TP / saran sizing / confidence / rationale ber-sitasi). **Tidak mengeksekusi order** — eksekusi manual oleh user.

> Dokumen induk: [`../IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md) (preskriptif — wajib dibaca sebelum coding) dan riset di `../DEEP_RESEARCH_TRADING_AI.md`.

## Prinsip emas

**ANGKA dari kode deterministik, NARASI dari LLM.** LLM hanya boleh CONFIRM / VETO / ABSTAIN — tidak pernah menghasilkan atau mengubah angka harga (guardrail GR-10).

## Quickstart (dev)

```bash
# prasyarat: Python 3.12, uv, Docker
cp .env.example .env          # isi placeholder seperlunya (P0 tidak butuh API key)
uv sync                       # install deps + project (editable)
docker compose up -d          # TimescaleDB + Redis
uv run alembic upgrade head   # apply schema
uv run pytest -q              # unit + integration (integration auto-skip jika DB mati)
```

## Perintah

| Make (Linux/VPS) | Windows PowerShell | Fungsi |
|---|---|---|
| `make dev` | `docker compose up -d` | start TimescaleDB + Redis |
| `make lint` | `uv run ruff check src tests migrations; uv run ruff format --check src tests migrations` | lint + format check |
| `make typecheck` | `uv run mypy` | mypy strict pada `src/` |
| `make test-unit` | `uv run pytest tests/unit -q` | unit tests |
| `make test-integration` | `uv run pytest -m integration -q` | butuh stack dev hidup |
| `make migrate` | `uv run alembic upgrade head` | migrasi DB |
| `make ci` | jalankan ketiganya: lint, typecheck, test-unit | gate sebelum commit |

## Struktur

```
config/          # SEMUA threshold & instrumen (bukan hardcode) — divalidasi saat load
src/rtrade/
  core/          # config loader, constants (enum), errors, timeutil (UTC-only)
  persistence/   # SQLAlchemy models (= DDL PLAN §10), repositories, session
migrations/      # Alembic (TimescaleDB hypertable utk candles)
docs/adr/        # Architecture Decision Records (ADR-001..012)
tests/           # unit / integration / golden
```

## Status fase

- [x] **P0 — Fondasi** (repo, tooling, compose, core config, DB schema, ADR, CI)
- [ ] P1 — MVP deterministik (data, indikator, S1, risk, guardrails, backtest, Telegram)
- [ ] P2 — Otak AI + anti-halusinasi (LiteLLM, analyst/critic/verifier)
- [ ] P3 — Multi-provider, S2, HMM, dashboard
- [ ] P4 — Production hardening + kalibrasi paper 4–8 minggu

## Disclaimer

⚠️ Bukan nasihat keuangan. Trading berisiko tinggi; 74–89% akun retail merugi (data ESMA). Sinyal adalah alat bantu analisa terkalibrasi, bukan ramalan.
