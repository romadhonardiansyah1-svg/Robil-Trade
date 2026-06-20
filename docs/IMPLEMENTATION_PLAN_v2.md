# Robil Trade v2 — Implementation Plan (Stabilization & Hardening)

> **Dokumen ini adalah instruksi kerja lengkap untuk agent pelaksana.** Setiap item P0→P3 punya format konsisten: **Why → Context (root cause, `file:line`) → What changes → Code (snippet siap pakai) → Verification command → Acceptance test stub → Known pitfalls → FR/NFR/GAP refs**. Baca Bagian 0–2 dulu sebelum sentuh kode apa pun.
>
> **Sumber kebenaran otoritatif:** `C:/Robil Trade/ROBIL_TRADE_PRD.md` (1992 baris). Bila ada kontradiksi antara dokumen ini dan PRD, **PRD menang** — baca ulang FR/NFR/ADR yang relevan lalu sesuaikan.
>
> **Disusun:** 2026-06-19 dari review mendalam kode hidup + PRD §1-17 + `research/ATM_FINCEPT_CATALOG.md`. Kondisi kode (`file:line`) akurat per review tetapi **bisa drift** saat Anda mengedit — selalu baca ulang file sebelum mengubah.

---

## DAFTAR ISI

- Bagian 0 — Aturan Main (Baca Pertama)
- Bagian 1 — Global Invariants (berlaku untuk SETIAP perubahan)
- Bagian 2 — Knowledge Base (arsitektur saat ini, file:line map, konvensi)
- **PHASE P0** — Stabilize: Unblock Non-Crypto + Legal Guardrail
- **PHASE P1** — Reliability + Dormant Safety + Go-Live Gate
- **PHASE P2** — Accuracy, Cost Controls, Observability
- **PHASE P3** — Enhancements (accuracy-positive only)
- Bagian Akhir — Test Pyramid, CI, Coverage, Checklist, Traceability Matrix

---

# Bagian 0 — Aturan Main (Baca Pertama, Non-Negotiable)

## 0.1 Apa itu Robil Trade v2

Robil Trade adalah **bot signal-only** (Python 3.12) untuk eksekusi MANUAL. Ia menghasilkan ide trading ber-audit-penuh dan ber-risiko-terbatas lalu mengirimnya ke channel Telegram, didukung permukaan FastAPI baca/kontrol. **TIDAK menempatkan order, tidak auto-execution, tidak broker integration** (ADR-001).

v2 adalah rilis **stabilisasi + hardening**, BUKAN rewrite (ADR-A01). Pipeline deterministik sudah lengkap, mypy-strict, hash-chain audited, dan config-validated. Anda hanya **menutup gap-gap spesifik yang dormant/external/unwired**.

## 0.2 Tiga aturan yang TIDAK BOLEH dilanggar (bila ragu, stop)

1. **Signal-only, manual execution.** Tidak ada `place_order`, `cancel_order`, broker client, atau loop execution. Setiap istilah "fill"/"exit"/"SL_HIT"/"TP_HIT" di kode adalah **outcome virtual paper** (kalibrasi), bukan order live. Setiap pesan terkirim **wajib** menyertakan disclaimer Bahasa-Indonesia (`DISCLAIMER_TEXT` di `src/rtrade/signals/schemas.py`).
2. **Fail-CLOSE adalah default** untuk gate kalender. `calendar.fail_open_when_stale` tetap `false`. Jangan pernah trade buta terhadap berita.
3. **Risk-safety floors non-negotiable**: GR-03 RR≥1.5, GR-04 SL∈[0.5,3.0]×ATR, GR-05 risk≤2%. Config yang melemahkan floor mana pun **wajib gagal load** (sudah diterapkan di `src/rtrade/core/config.py`). Jangan pernah melemahkan.

## 0.3 Environment & tooling (sudah setup, JANGAN ubah)

- Python `>=3.12,<3.13`. Package manager `uv` (lockfile `uv.lock`). Build backend hatchling. Layout `src/rtrade/`.
- **mypy strict** pada package `rtrade` (`[tool.mypy] strict=true`). Tidak ada exemption untuk kode baru.
- **Ruff**: line-length 100, target py312, select termasuk `DTZ, T20, PLE`. `ruff check` + `ruff format --check` harus hijau.
- **pytest**: `asyncio_mode="auto"`, `testpaths=["tests"]`, marker `integration`.
- Library test: `freezegun` (time), `respx` (HTTP mock), `hypothesis` (property). **Tidak ada tes yang boleh hit live network** kecuali tier eksplisit `make smoke`.

## 0.4 Branch discipline

- Buat branch `release/v2-stabilization` dari `main`. PR ke `main`.
- Commit per-fase, per-item, pesan konvensional: `feat(calendar):`, `fix(guardrails):`, `test(selftest):`, `chore(config):`, `docs(adr):`.
- **Jangan pernah commit `.env`** (sudah di `.gitignore`; verifikasi dengan `git status`).
- Hapus file artifact Windows `nul` yang muncul di working tree (redirect `> nul` yang salah) sebelum commit.

## 0.5 Aturan "Verify Before Claim" (dari pemilik, NON-NEGOTIABLE)

Setelah **setiap item**, jalankan subset tes relevan dan **paste output asli**.
Setelah **setiap fase**, jalankan `make ci` (atau pipeline ordered ekuivalen) dan **paste output**.

Pipeline `make ci` ordered (cepat→lambat):
```bash
make ci    # atau manual:
# 1. ruff check
# 2. ruff format --check
# 3. mypy --strict src/
# 4. pytest tests/unit tests/property
# 5. pytest tests/integration
# 6. pytest --cov (dengan per-package coverage floors)
# 7. guardrail selftest job (semua 13 gate)
# 8. uv lock --check + audit
# 9. gitleaks scan
```

**Jangan pernah klaim "done" tanpa output hijau.** Jika gagal, perbaiki dulu.

## 0.6 Cara baca dokumen ini untuk agent pelaksana

- Ikuti **execution order** di Bagian Akhir (§9.7) secara ketat. Jangan lompat fase sebelum exit gate fase sebelumnya terverifikasi.
- Setiap item punya **FR/NFR/GAP refs** di akhir — itu link ke PRD. Bila bingung, buka PRD section itu.
- Snippet kode bertanda `[CODE]` adalah **siap pakai** tetapi tetap re-read file tujuan sebelum edit (line number bisa drift).
- Test stub bertanda `[TEST]` adalah kerangka; lengkapi assertion sesuai acceptance criteria.
- Simbol: ⚠️ = pitfall/edge case. 🔴 = release-blocking. 🟠 = pre-LLM-enablement. 🟡 = accuracy.

---

# Bagian 1 — Global Invariants (berlaku untuk SETIAP perubahan, SETIAP fase)

> **GI-1 mypy `--strict` HARUS hijau** untuk semua `src/`. Provider kalender baru, backtest CLI, budget guard — tidak ada exemption. Bila mypy complain tentang kode library eksternal, pakai `# type: ignore[import-untyped]` **hanya** dengan justifikasi inline (lihat pola di `scheduler/main.py` untuk `apscheduler`).

> **GI-2 Ruff `check` + `format --check` HARUS hijau.** Tidak boleh tambah per-file-ignore untuk kode baru tanpa komentar justifikasi inline.

> **GI-3 Disiplin test determinism.** Setiap tes yang menyentuh waktu → `freezegun.freeze_time(...)`. Setiap tes yang menyentuh HTTP → `respx` dengan recorded fixtures. Tidak ada live network. Pesan error tes harus spesifik (assert dengan message).

> **GI-4 Tidak menyalin FinceptTerminal** (ADR-A10 / G-21 / NFR-CI-04). Semua ide di-re-implement dari paper/pubspec atau di atas library permissive (`ta`/`pandas_ta`, `ccxt`, `httpx`, `tenacity`, `litellm`, `quantstats`/`empyrical`, `river`, `alternative.me`). Bila ada CI licensing guards (grep provenance string), jangan trip.

> **GI-5 Frozen `SignalCandidate`.** Invariant GR-02/03/04 diterapkan di Pydantic saat konstruksi. JANGAN pernah pakai `model_construct(...)` untuk bypass, jangan pernah mutate field frozen setelah konstruksi. Bila perlu "kandidat yang dimutasi LLM", buat objek baru (untuk perbandingan GR-10).

> **GI-6 `llm.enabled: false`** sepanjang P0/P1/P2. Hanya boleh flip `true` di **staging** di akhir P2. **Tidak pernah** production sebelum exit P2 + paper-track expectancy terpenuhi.

> **GI-7 Exit gate tiap fase = HARD acceptance gate.** Jangan mulai fase berikutnya sebelum exit criteria fase ini terverifikasi dengan output `make ci`.

> **GI-8 Konvensi kode.** Match comment density, naming, dan idiom file yang Anda edit. Modul pakai `from __future__ import annotations`. Dataclass domain: `frozen=True, slots=True`. Logging: `structlog.get_logger(__name__)`. Error: pakai taksonomi di `core/errors.py` (lihat Bagian 2.5).

> **GI-9 Tidak ada magic number hardcoded** bila bisa config-driven. Threshold go-live dibaca dari `settings.yaml backtest.gates` (parse ekspresi string), bukan hardcoded di CLI.

> **GI-10 Anti-lookahead mutlak.** Drop forming bars (`last_closed_candle_open`). Backtest signals hanya lihat `df.iloc[:i+1]`. Decide-on-close, fill-next-open. SL-first pada bar ambigu. Re-read `data/ingestion.py` dan `backtest/engine.py` untuk konvensi.

---

# Bagian 2 — Knowledge Base (baca sebelum sentuh kode)

## 2.1 Pipeline order deterministik (`pipeline/scan.py:run_scan`)

```
scheduler cron fires scan_job(symbol, tf)
  └─> run_scan():
      1.  get_or_create instrument row (Postgres) — InstrumentRepo(session).get_or_create(...)
      2.  INGEST: _ingest_incremental → ingest_candles
            provider.fetch_ohlcv → validate → drop forming bars → CandleRepo.upsert_many
            (Redis token-bucket rate-limited; tenacity transient-only retry)
      3.  (tf != H1) → return "ingested_context_only" (signal hanya dari H1)
      4.  (tf == H1 and H4 in timeframes) → ingest H4 context bila due
      5.  LOAD latest ~500 bars {1h, 4h} via CandleRepo.latest_n
      6.  (len(df_1h) < WARMUP) → return "abstain_warmup" / "insufficient_data"
      7.  INDICATORS: compute_indicators(df) via asyncio.to_thread  (P0 fix #1)
      8.  REGIME: _REGIME_CLASSIFIER.classify(symbol, df_1h)  (singleton, P0 fix #2)
            + HMM shadow → REGIME_SHADOW audit (TIDAK drive selection)
      9.  STRUCTURE: detect_swing_points / cluster_sr_levels / detect_gaps (to_thread)
     10.  CALENDAR: EventRepo.get_window(now-2h, now+72h) → news_blackout
            calendar_ts = CalendarSourceHealthRepo.freshest_success()  (P0 fix #8)
            calendar_stale = (non-crypto) and (calendar_ts None or > stale_after_hours)
                              and not fail_open_when_stale
     11.  LIVE QUOTE: provider.fetch_quote → live_price  (GR-06; fail-CLOSE bila None — P1 fix)
     12.  (crypto) DERIVATIVES: funding rate + OI → DerivativesSnapshot
     13.  SPREAD: provider.fetch_spread (None untuk FX/metals)
     14.  _run_strategies(...): per enabled strategy matching regime
            generate_candidate → levels validate/round → edge-quality filter
              → confluence ≥ min → risk sizing → FROZEN SignalCandidate
     15.  AUDIT candidate → dedup check (instrument/tf/strategy/bar_ts) → daily-count
     16.  run_gate() — 13 guardrails (GR-09/10/11 dormant di deterministic path — P1 fix)
            fail → persist REJECTED (+ GR-13 auto-disable strategy) → STOP
     17.  [if llm.enabled — DISABLED] context pack → analyst → critic → verifier
            → confidence → doubt-band flagship escalation
            → post-LLM run_gate (GR-09/10/11 wired — P1 fix)
     18.  GRADE A/B/C via grade_signal (edge_quality_score=None today — P2 fix)
            → size multiplier (risk_multiplier); optional Kelly (≥30 trades)
     19.  PERSIST PUBLISHED Signal (JSONB payload) + hash-chained audit
     20.  FORMAT Bahasa-Indonesia message + mandatory disclaimer → Telegram send → delivery audit

  paper_track_job (every 15 min, independent):
      replay open signals over closed candles (SL-first; crypto minute-resolution)
      → virtual-exit ensemble + MAE/MFE → [coroner if SL_HIT & enabled]
      → outcome_r → feeds GR-13, risk throttle, grading, Kelly, k-NN
```

## 2.2 File map otoritatif (re-read sebelum edit)

| Area | File | Catatan penting |
|---|---|---|
| Config model | `src/rtrade/core/config.py` | `CalendarSettings:59`, `Settings:147`, `AppConfig.load:302`, `instrument():325`, floor validators `:24-28`. `_StrictModel` = `extra="forbid"`. |
| Config files | `config/settings.yaml`, `config/instruments.yaml`, `config/costs.yaml`, `config/strategies/*.yaml` | instruments: 6 simbol, timeframes `[1h,4h]`, context_tf `1d`. costs hanya 3/6 simbol. |
| Errors | `src/rtrade/core/errors.py` | `RTradeError` base; `ConfigError`, `DataValidationError`/`StaleDataError`, `ProviderError`/`RateLimitExceeded`, `StorageError`, `LLMOutputError`/`LLMUnavailableError`, `GuardrailViolation`. |
| Time utils | `src/rtrade/core/timeutil.py` | `ensure_utc`, `last_closed_candle_open`, `is_candle_fresh`, `timeframe_duration`, `utcnow`. Semua datetime UTC-aware. |
| Data ABCs | `src/rtrade/data/base.py` | `EconomicEvent:77` (frozen+slots, `__post_init__` validate impact+UTC), `CalendarProvider:158` (`fetch_events(start,end)`, `close`), `MarketDataProvider:125`, `DerivativesProvider:177`. |
| Finnhub (existing, benar) | `src/rtrade/data/finnhub_calendar.py` | `403 → ProviderError` (paid-only), `429 → RateLimitExceeded`, `_ALWAYS_HIGH_EVENTS:36`, `_COUNTRY_TO_CURRENCY:51`, `_event_id`, `_normalize_impact`. |
| Rate limiter | `src/rtrade/data/ratelimit.py` | Redis Lua token-bucket, server-side `TIME` (P0 fix #4), timeout 25s. Buckets: `TWELVEDATA_BUCKET=7/min`, `CCXT_BINANCE=1000/min`, `FINNHUB=50/min`, `BINANCE_PUBLIC=500/min`. |
| Ingestion | `src/rtrade/data/ingestion.py` | `ingest_candles` (drop forming bars), `detect_gaps` (rename→`detect_candle_gaps` P2 fix). |
| Providers market | `twelvedata_provider.py`, `ccxt_provider.py` | transient-only retry (P0 fix #4). FX no-spread. |
| Pipeline | `src/rtrade/pipeline/scan.py` | `run_scan:162`, `_ingest_incremental:130`, `sync_calendar:384`, `track_paper_signals`, `_run_strategies`, `_make_market_provider`, `_get_engine/_get_redis` (singletons P0 fix #6). |
| Indicators | `indicators/engine.py`, `indicators/structure.py` | `compute as compute_indicators` (pure), `snapshot as indicator_snapshot`. structure: `detect_swing_points`, `cluster_sr_levels`, `detect_gaps` (FVG, collide dgn ingestion — P2 rename). |
| Regime | `regime/rules.py`, `regime/hmm.py` | `RegimeClassifier` (singleton P0 fix #2), hysteresis ADX 20-25, CRISIS=atr_pct≥95 OR \|return_24h\|≥3σ (1-bar bug — P2 fix true 24-bar). HMM shadow. |
| Signals | `signals/engine.py` (generate_candidate), `confluence.py`, `levels.py`, `edge_quality.py`, `grading.py`, `schemas.py` | `assess_edge_quality:53 → EdgeQualityReport.score` (dipanggil dgn None di grade — P2 fix). `grade_signal:30` (edge_quality_score param ada). |
| Strategies | `strategies/base.py`, `s1_trend_pullback.py`, `s2_range_mr.py`, `strategies/__init__.py` | `STRATEGY_REGISTRY`. S1=TREND, S2=RANGE. |
| Risk | `risk/sizing.py`, `kelly.py`, `limits.py`, `news_filter.py` | `check_daily_limit`, `check_expectancy_guard`, `check_news_blackout`, `high_impact_within`. |
| Guardrails | `guardrails/gate.py`, `guardrails/selftest.py` | `run_gate` 13 gates (GR-09/10/11 guarded `if param is not None`); `run_guardrail_selftest()` return list[str] (hanya 3/13 gate — P1 fix). |
| Persistence | `persistence/db.py`, `models.py`, `repositories.py`, `audit_chain.py` | `create_engine` (pool params P0 fix), async sessionmaker. `EconomicEvent:67`, `Signal`, `BacktestRun:147`, `CalendarSourceHealth` (baru P0). Repos: `CandleRepo`, `EventRepo` (row-by-row merge), `SignalRepo`, `InstrumentRepo`, `AuditRepo`, `StrategyStateRepo`. |
| Scheduler | `scheduler/main.py`, `scheduler/jobs.py` | `AsyncIOScheduler(UTC)`, `build_scan_schedules`, `run_worker` (selftest + shutdown hooks). `scan_job`, `calendar_sync_job`, `paper_track_job`, `health_check_job`, `hmm_train_job`. `_run_job` wrapper (P0 fix #11), `_send_failure_alert` (retire di P2). |
| Delivery | `delivery/telegram_bot.py`, `delivery/formatter.py`, `delivery/api/app.py`, `routes.py` | Push OK; commands stub (P3 fix). Bearer auth + throttle. |
| Monitoring | `monitoring/healthcheck.py`, `monitoring/alerts.py` | `AlertManager` (imported nowhere — P2 adopt). `check_disk('/')` (P2 per-OS). |
| LLM | `llm/pipeline.py`, `analyst.py`, `critic.py`, `verifier.py`, `cascade.py`, `context_pack.py`, `client.py`, `key_manager.py`, `model_router.py`, `auth/*` | cascade analyst→critic→verifier, disabled by default. budget guard baru P2. |
| Backtest | `backtest/engine.py`, `walkforward.py`, `permutation.py`, `metrics.py`, `validation.py`, `costs.py`, `harness.py`, `smart_exit.py` | `generate_windows`, `run_walk_forward`, `run_walkforward_harness`, `run_validation_gates`, `deflated_sharpe_ratio`, `probability_of_backtest_overfit`. `harness.py:4` docstring → non-existent `scripts/run_backtest.py`. |
| CLI | `cli/auth.py`, `cli/backfill.py`, `cli/bot.py` | argparse. Tidak ada `cli/backtest.py` (P1 build). Tidak ada `[project.scripts]` (P3). |

## 2.3 Error hierarchy (`core/errors.py`)

```
Exception
└── RTradeError
    ├── ConfigError                 # invalid/missing/guardrail-weakening config
    ├── DataValidationError
    │   └── StaleDataError          # GR-06 data older than freshness limit
    ├── ProviderError               # upstream HTTP/schema/auth failure
    │   └── RateLimitExceeded       # local token bucket exhausted
    ├── StorageError
    ├── LLMOutputError              # LLM output gagal schema validation
    ├── LLMUnavailableError         # semua provider LLM gagal
    └── GuardrailViolation          # gate_id + reason
```
**Aturan pakai:** transient (`httpx.TransportError`) → bounded retry (tenacity, transient-only). `429 → RateLimitExceeded` (bucket handle, jangan double-wait). `4xx/5xx → ProviderError` (no retry). Config invalid → `ConfigError` (fail-load). Naive datetime → `DataValidationError`.

## 2.4 Konvensi penting (WAJIB ikut)

- **Decimal vs float**: `Candle` OHLC `Decimal` (domain/DB); indicators/levels/R-multiple `float` setelah `_candles_to_df`. Jangan campur.
- **Idempotency**: re-ingest/re-scan idempotent. Dedup keys: candles `(instrument, timeframe, ts)`, signals `(instrument, timeframe, strategy, bar_ts)`, events `event_id`.
- **Provider selection**: murni dari `instrument.provider` string di `_make_market_provider` (scan.py). Switch provider di `instruments.yaml` = no code change.
- **Singletons process-scoped** (P0 fix #6): `_REGIME_CLASSIFIER` (regime), `_get_engine`/`_get_redis` (DB/Redis), `_SCAN_POOL_CACHE` (LLM cred pool), `_HMM_CACHE`. Dispose via `shutdown_process_resources` di `run_worker` finally.

## 2.5 Cara menguji tanpa live network

- **HTTP**: `respx` mock `httpx.AsyncClient` dengan recorded fixtures. Untuk provider kalender baru, record **satu** fixture real (manual via curl) lalu replay selamanya.
- **Time**: `freezegun.freeze_time("2026-06-19T12:00:00Z")` di decorator/context manager.
- **DB**: pytest fixture dengan Postgres container (CI) atau sqlite-in-memory bila test tidak butuh PG-specific. Repository tests pakai real `AsyncSession`.
- **Redis**: `fakeredis.aioredis` atau container. Rate limiter tests pakai fakeredis agar clock-skew terkontrol.
- **LLM**: mock `LLMClient` / `run_llm_pipeline` — jangan enable `llm.enabled`.

## 2.6 P0+P1 stability fixes — STATUS (post-audit, most NOT yet implemented)

> Audit post-audit found these were marketed as done but are pending; see remediation phases.

Branch `fix/bot-stability-accuracy` was claimed to contain the items below. Verified
status against the actual code:
- **#1** CPU sync offload via `asyncio.to_thread` in `run_scan` (indicators, regime, structure) — **NOT IMPLEMENTED** (no `to_thread`/`run_in_executor` in `src`; `scan.py` runs CPU work inline).
- **#2** `_REGIME_CLASSIFIER` singleton (hysteresis persist) — **NOT IMPLEMENTED** (`scan.py` constructs `RegimeClassifier()` per scan; hysteresis state never persists).
- **#3** Calendar fail-close safety net + startup validation — **PARTIAL** (`fail_open_when_stale=false` is honored, but startup logs CRITICAL at `main.py` and does NOT halt).
- **#4** Rate limiter: timeout 25s, server-side Redis `TIME` (clock-skew), remove double rate-limit retry in providers — **NOT IMPLEMENTED** (`ratelimit.py` uses client-side `time.time()`; no `TIME`, no timeout cap).
- **#5** Rate-limit alert dedup (don't suppress entirely) — **NOT WIRED** (`AlertManager` exists in `monitoring/alerts.py` but is imported nowhere; live path is `_send_failure_alert` without dedup).
- **#6** Engine/Redis/Regime singletons + graceful shutdown `shutdown_process_resources` — **NOT IMPLEMENTED** (no `_get_engine`/`_get_redis`; engines/redis created per call in `scan.py`/`jobs.py`).
- **#11** `_run_job` wrapper for non-scan jobs (error handling + alert) — **NOT IMPLEMENTED** (no `_run_job` in `scheduler/jobs.py`).
- **#15** Calendar key validation (Finnhub malformed key → 401/403 detected) — **UNVERIFIED**.

**This is NOT a completed baseline.** v2 (this document) and the remediation phases must
implement these items; do not assume they exist. Verify before relying on any item above.

## 2.7 Open question yang gating P0

**Pemilihan source kalender primer** (pemilik putuskan, catat di ADR/PR):
- (a) Mix sumber gratis (Investing JSON + Trading Economics + Nasdaq) — berisiko ToS/outage.
- (b) Tier berbayar murah (Finnhub paid / TE paid) — paling reliable untuk dependency fail-CLOSE.

**Rekomendasi dokumen ini:** ship composite (primary gratis + static fallback) di P0, evaluasi tier berbayar sebagai drop-in config-flag bila uptime miss SLA 99.5%. Tetap fail-CLOSE.

---

# PHASE P0 — STABILIZE: UNBLOCK NON-CRYPTO + LEGAL GUARDRAIL 🔴

> **Dominant goal:** buat `economic_events` terisi sehingga GR-07b berhenti fail-close SEMUA signal XAUUSD/EURUSD/GBPUSD/USDJPY. Hari ini bot efektif crypto-only. **Ini langkah highest-leverage di seluruh program.**
>
> **Root cause (CONFIRMED LIVE):** `data/finnhub_calendar.py` BENAR — Finnhub `/calendar/economic` adalah paid-only, free key return HTTP 403 (atau 401 bila malformed). Tabel `economic_events` tidak pernah terisi → GR-07b fail-close 100% signal non-crypto. **Sumber data, bukan kode, yang jadi blocker.** Fix ada di **data layer di balik `CalendarProvider` ABC** — gate tetap fail-CLOSE (ADR-A02).

## Item eksekusi P0 (urut)

| ID | Item | Prioritas |
|---|---|---|
| P0-1 | Record legal ADR-A10 | 🔴 wajib pertama |
| P0-2 | Extend config `calendar.sources` | 🔴 |
| P0-3 | Provider Investing (primary) | 🔴 core |
| P0-4 | Provider Static (last-resort) | 🔴 |
| P0-5 | CompositeCalendarProvider | 🔴 |
| P0-8 | CalendarSourceHealth table + repo | 🔴 (tarik maju dari P1) |
| P0-6 | Wire composite ke `sync_calendar` + verify GR-07b | 🔴 THE acceptance |
| P0-7 | Startup warning jika no calendar source | 🔴 |
| P0-EXIT | Verifikasi exit gate | 🔴 |

---

## P0-1 — Record legal ADR-A10 (G-21) 🔴

### Why
P0 acceptance condition (DEF-REQ-02). Kunci stance license SEBELUM kode apa pun, agar tidak ada risiko kontaminasi di downstream.

### Context
FinceptTerminal = AGPL-3.0 + restrictive commercial license (stated liquidated damages USD 50k/org/yr). Robil = bot komersial → bahkan AGPL personal-use tidak cover.

### What changes
- Verifikasi/buat `docs/adr/` (sudah ada per review).
- Tulis `docs/adr/ADR-A10-conceptual-reference-only.md`.
- Tulis/extend `docs/CODE_REVIEW_CHECKLIST.md` dengan checkbox ADR-A10.
- (Bila ada `.github/pull_request_template.md`, tambahkan checkbox juga.)

### Code — `docs/adr/ADR-A10-conceptual-reference-only.md`
```markdown
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
```

### Code — `docs/CODE_REVIEW_CHECKLIST.md`
```markdown
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
```

### Verification
```bash
ls docs/adr/ADR-A10-conceptual-reference-only.md
ls docs/CODE_REVIEW_CHECKLIST.md
git diff --stat
```

### Acceptance
- Kedua file ada. DEF-REQ-02 tercatat.

### Known pitfalls ⚠️
- Jangan tulis contoh kode Fincept di ADR — hanya konsep abstrak.
- Bila CI licensing guard grep string spesifik, pastikan tidak ada di diff.

### Refs
ADR-A10, G-21, DEF-REQ-02, NFR-LEG-02, NFR-MAINT-07, NFR-CI-04.

---

## P0-2 — Extend config `calendar.sources` (FR-CAL-07) 🔴

### Why
Provider injection butuh config-driven ordering + per-source enable flag agar source bisa di-swap tanpa kode (NFR-MAINT-01).

### Context
`CalendarSettings` (`core/config.py:59-70`) saat ini hanya 2 field:
```python
class CalendarSettings(_StrictModel):
    fail_open_when_stale: bool = False
    stale_after_hours: float = Field(default=18.0, gt=0.0)
```
`CalendarProvider` ABC di `data/base.py:158`:
```python
class CalendarProvider(ABC):
    @abstractmethod
    async def fetch_events(self, start: date, end: date) -> list[EconomicEvent]: ...
    @abstractmethod
    async def close(self) -> None: ...
```

### What changes
- Tambah `CalendarSourceConfig` model + field `sources`, `sync_lookback_days`, `sync_lookforward_days` ke `CalendarSettings`.
- Tambah field validator `_unique_source_names`.
- Update `config/settings.yaml` block `calendar:`.

### Code — edit `src/rtrade/core/config.py`
[CODE] ganti seluruh definisi `CalendarSettings` dengan:
```python
class CalendarSourceConfig(_StrictModel):
    """Satu sumber kalender dalam rantai composite (FR-CAL-07)."""

    name: str  # "investing" | "nasdaq" | "trading_economics" | "static_high_impact" | "finnhub"
    enabled: bool = True


class CalendarSettings(_StrictModel):
    """Konfigurasi lapisan kalender ekonomi (GR-07b dependency).

    Default fail-CLOSE (fail_open_when_stale=false) — bot tidak pernah trade
    buta terhadap berita. Mem-flip ke true WAJIB keputusan operator eksplisit
    yang di-logging WARNING keras.
    """

    fail_open_when_stale: bool = False
    stale_after_hours: float = Field(default=18.0, gt=0.0)
    sync_lookback_days: int = Field(default=1, ge=0)
    sync_lookforward_days: int = Field(default=7, ge=1)
    sources: list[CalendarSourceConfig] = Field(
        default_factory=lambda: [CalendarSourceConfig(name="static_high_impact", enabled=True)]
    )

    @field_validator("sources")
    @classmethod
    def _unique_source_names(cls, v: list[CalendarSourceConfig]) -> list[CalendarSourceConfig]:
        names = [s.name for s in v]
        if len(set(names)) != len(names):
            raise ValueError(f"calendar.sources names must be unique, got {names}")
        if not v:
            raise ValueError("calendar.sources must not be empty (at least static_high_impact)")
        return v
```
⚠️ Confirm `field_validator` import sudah ada (pydantic v2). Re-read file head.

### Code — edit `config/settings.yaml`
[CODE] ganti block `calendar:` yang ada dengan:
```yaml
calendar:
  fail_open_when_stale: false
  stale_after_hours: 18
  sync_lookback_days: 1
  sync_lookforward_days: 7
  sources:
    - { name: investing, enabled: true }
    - { name: static_high_impact, enabled: true }
```

### Verification
```bash
mypy --strict src/rtrade/core/config.py
pytest tests/unit/test_config.py -k calendar -x
ruff check src/rtrade/core/config.py
```

### Acceptance [TEST] — `tests/unit/test_config.py`
```python
def test_calendar_sources_round_trip():
    cfg = AppConfig.load(config_dir="config", env_file=None)
    names = [s.name for s in cfg.settings.calendar.sources]
    assert "investing" in names
    assert "static_high_impact" in names
    assert cfg.settings.calendar.fail_open_when_stale is False
    assert cfg.settings.calendar.stale_after_hours == 18.0


def test_calendar_sources_unique():
    from rtrade.core.config import CalendarSettings, CalendarSourceConfig
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CalendarSettings(sources=[
            CalendarSourceConfig(name="investing"),
            CalendarSourceConfig(name="investing"),
        ])


def test_calendar_fail_open_default_false():
    from rtrade.core.config import CalendarSettings
    s = CalendarSettings()
    assert s.fail_open_when_stale is False
```

### Known pitfalls ⚠️
- `_StrictModel` punya `extra="forbid"` → field baru WAJIB ada di model atau load fail.
- Jangan ubah default `fail_open_when_stale: false`.
- Validator field di pydantic v2 = `@field_validator("x")`, bukan `@validator("x")`.

### Refs
FR-CAL-07, NFR-MAINT-01, NFR-REL-03.

---

## P0-3 — Provider Investing (PRIMARY) 🔴

### Why
Source kalender gratis low-dependency di balik ABC untuk menggantikan Finnhub paid-only.

### Context
Mirror pola `finnhub_calendar.py`. ABC `CalendarProvider.fetch_events(start: date, end: date) -> list[EconomicEvent]`. `EconomicEvent` fields: `event_id, event, currency, impact ∈ {low,medium,high}, event_time(UTC), actual?, forecast?, previous?, fetched_at`.

### Source decision (catat di PR/ADR)
**Investing.com public economic-calendar JSON endpoint** via `httpx` HTTP/2 (ADR-A04: no Selenium). Bila Investing unreliable saat verify P0-6, swap ke Trading Economics / Nasdaq — ABC + config-driven ordering = no other code change.

> ⚠️ Investing.com kadang butuh header browser-like + `Domain-ID`. Uji sekali manual via curl untuk record fixture.

### Code — `src/rtrade/data/investing_calendar.py`
[CODE] (sesuaikan endpoint/param nyata setelah curl probe):
```python
"""Investing.com economic-calendar provider (independent re-implementation).

Low-dependency httpx JSON client behind the CalendarProvider ABC (ADR-A02).
Replaces the paid-only Finnhub /calendar/economic (HTTP 403 on free tier).
No Selenium/browser automation (ADR-A04). All parsing re-implemented from the
public endpoint shape; no third-party GPL/AGPL code (ADR-A10).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import hashlib

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.core.text_sanitize import sanitize_event_text
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.investing.com"  # confirm real host via curl probe
_DEFAULT_TIMEOUT = 15.0

_ALWAYS_HIGH_EVENTS: set[str] = {
    "fomc", "federal funds rate", "fed interest rate decision",
    "nonfarm payrolls", "non-farm payrolls", "nfp",
    "cpi", "consumer price index",
    "ecb interest rate decision", "ecb rate decision", "ecb monetary policy",
}

_COUNTRY_TO_CURRENCY: dict[str, str] = {
    "US": "USD", "EU": "EUR", "EZ": "EUR", "DE": "EUR", "FR": "EUR",
    "IT": "EUR", "ES": "EUR", "NL": "EUR", "GB": "GBP", "UK": "GBP",
    "JP": "JPY", "CH": "CHF", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "CN": "CNY",
}


def _to_currency(raw: str) -> str:
    return _COUNTRY_TO_CURRENCY.get(raw.strip().upper(), raw.strip().upper())


def _normalize_impact(raw_impact: str | int, event_name: str) -> str:
    name_lower = event_name.lower()
    for kw in _ALWAYS_HIGH_EVENTS:
        if kw in name_lower:
            return "high"
    if isinstance(raw_impact, int):
        if raw_impact >= 3: return "high"
        if raw_impact == 2: return "medium"
        return "low"
    s = str(raw_impact).lower()
    if s in ("high", "3", "bullish"): return "high"
    if s in ("medium", "2"): return "medium"
    return "low"


def _event_id(event_name: str, event_time: str, currency: str) -> str:
    return hashlib.sha256(f"investing:{event_name}:{event_time}:{currency}".encode()).hexdigest()[:16]


def _safe_decimal(val: object) -> Decimal | None:
    if val is None or val == "": return None
    try: return Decimal(str(val))
    except (InvalidOperation, ValueError): return None


class InvestingCalendarProvider(CalendarProvider):
    """FR-CAL-01. Keyless. Transient-only retry. Sanitizes event names."""

    def __init__(self, *, http_timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._http = httpx.AsyncClient(
            base_url=_BASE_URL, timeout=http_timeout, http2=True,
            headers={
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
                "Accept": "application/json", "Domain-ID": "www",
            },
        )

    @retry(retry=retry_if_exception_type(httpx.TransportError),
           wait=wait_exponential(multiplier=1, min=1, max=8),
           stop_after_attempt=3), reraise=True)
    async def _get(self, path: str, params: dict[str, object]) -> httpx.Response:
        return await self._http.get(path, params=params)

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        params = {"dateFrom": start.isoformat(), "dateTo": end.isoformat(), "timeframe": "60"}
        try:
            resp = await self._get("/api/financialcalendar", params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Investing calendar HTTP error: {exc}") from exc

        if resp.status_code == 429: raise RateLimitExceeded("Investing 429")
        if resp.status_code in (401, 403): raise ProviderError(f"Investing {resp.status_code}: denied")
        if resp.status_code >= 400:
            raise ProviderError(f"Investing HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        raw_events = body.get("data") or body.get("events") or body.get("economicCalendar") or []
        if not raw_events:
            logger.info("Investing returned no events", start=start.isoformat(), end=end.isoformat())
            return []

        events: list[DomainEvent] = []
        now = datetime.now(UTC)
        for row in raw_events:
            try:
                event_name = sanitize_event_text(str(row.get("event", "") or ""))
                currency = _to_currency(str(row.get("country") or row.get("currency") or ""))
                impact = _normalize_impact(row.get("impact", row.get("importance", 1)), event_name)
                date_str = str(row.get("date", "")); time_str = str(row.get("time", "00:00:00"))
                if not event_name or not date_str: continue
                full_dt_str = f"{date_str} {time_str}"
                event_time = datetime.strptime(full_dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                events.append(DomainEvent(
                    event_id=_event_id(event_name, full_dt_str, currency),
                    event=event_name, currency=currency, impact=impact, event_time=event_time,
                    actual=_safe_decimal(row.get("actual")),
                    forecast=_safe_decimal(row.get("forecast") or row.get("estimate")),
                    previous=_safe_decimal(row.get("previous") or row.get("prev")),
                    fetched_at=now,
                ))
            except (ValueError, KeyError) as exc:
                logger.warning("skipping invalid Investing event", error=str(exc), row=row)

        logger.info("investing calendar fetched", start=start.isoformat(), end=end.isoformat(),
                    total=len(events), high_impact=sum(1 for e in events if e.impact == "high"))
        return events

    async def close(self) -> None:
        await self._http.aclose()
```

### Code — `src/rtrade/core/text_sanitize.py` (bila belum ada)
[CODE] (re-read dulu `llm/context_pack.py`/`llm/sanitize.py` — bila sudah ada helper, PAKAI itu):
```python
"""Prompt-injection sanitization untuk text dari sumber eksternal (FR-CAL-06).

Dipakai di ingestion kalender DAN context-pack LLM (defense-in-depth).
Implementasi sendiri (ADR-A10), no GPL/AGPL source.
"""
from __future__ import annotations
import re

_MAX_LEN = 200
_INJECTION_PATTERNS = re.compile(
    r"(?i)\b(ignore (all|previous|the) (instructions?|prompts?)|"
    r"system\s*[:\-]|assistant\s*[:\-]|you are (now )?a|"
    r"</?(system|prompt|instruction)|```\w*)"
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\r\n\t]+")


def sanitize_event_text(raw: str, *, max_len: int = _MAX_LEN) -> str:
    if not raw: return ""
    cleaned = _CONTROL_CHARS.sub(" ", raw).strip()
    cleaned = _INJECTION_PATTERNS.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]
```

### Verification
```bash
curl -s "https://api.investing.com/api/financialcalendar?dateFrom=2026-06-16&dateTo=2026-06-20&timeframe=60" \
  -H "User-Agent: Mozilla/5.0" -H "Domain-ID: www" -H "Accept: application/json" \
  > tests/fixtures/investing_calendar_2026_06.json
mypy --strict src/rtrade/data/investing_calendar.py src/rtrade/core/text_sanitize.py
pytest tests/unit/test_investing_calendar.py -x --cov=rtrade/data/investing_calendar --cov-fail-under=80
ruff check src/rtrade/data/investing_calendar.py src/rtrade/core/text_sanitize.py
```

### Acceptance [TEST] — `tests/unit/test_investing_calendar.py`
```python
import respx, httpx
from datetime import date
from pathlib import Path
from rtrade.data.investing_calendar import InvestingCalendarProvider

FIXTURE = Path("tests/fixtures/investing_calendar_2026_06.json").read_text()

@respx.mock
async def test_fetch_events_high_impact_fomc():
    respx.get("https://api.investing.com/api/financialcalendar").mock(
        return_value=httpx.Response(200, text=FIXTURE))
    async with InvestingCalendarProvider() as p:
        events = await p.fetch_events(date(2026, 6, 17), date(2026, 6, 18))
    assert len(events) >= 1
    assert all(e.impact in {"low", "medium", "high"} for e in events)
    assert all(e.event_time.tzinfo is not None for e in events)

@respx.mock
async def test_429_raises_rate_limit():
    respx.get("https://api.investing.com/api/financialcalendar").mock(
        return_value=httpx.Response(429))
    from rtrade.core.errors import RateLimitExceeded
    async with InvestingCalendarProvider() as p:
        try:
            await p.fetch_events(date(2026, 6, 17), date(2026, 6, 18))
            assert False, "should raise"
        except RateLimitExceeded: pass
```

### Known pitfalls ⚠️
- **Endpoint shape drift** → parsing defensif (try/except per-row + skip). Test pakai fixture beku.
- `extra="forbid"` di Pydantic: field `DomainEvent` harus match persis.
- `http2=True` butuh `h2` dep. Bila belum: `uv add h2` atau drop `http2=True`.
- Jangan pakai `requests` (sync). ABC async.
- Sanitize SEBELUM konstruksi `DomainEvent`.

### Refs
FR-CAL-01, FR-CAL-06, FR-DATA-07, NFR-SEC-07, NFR-PERF-03, ADR-A02, ADR-A04, ADR-A10.

---

## P0-4 — Provider Static (last-resort) 🔴

### Why
Tier terminal composite — agar GR-07b **tidak pernah indefinitely blind** pada event yang paling penting (FOMC/NFP/CPI/ECB). Zero network calls (NFR-SCALE-03).

### Code — `config/static_calendar.json`
[CODE] (lengkapi untuk 12 bulan, refresh kuartalan):
```json
{
  "version": "2026.06",
  "events": [
    {"event": "FOMC Rate Decision", "currency": "USD", "time": "2026-07-30T18:00:00Z"},
    {"event": "FOMC Rate Decision", "currency": "USD", "time": "2026-09-17T18:00:00Z"},
    {"event": "Non-Farm Payrolls", "currency": "USD", "time": "2026-07-03T12:30:00Z"},
    {"event": "Non-Farm Payrolls", "currency": "USD", "time": "2026-08-07T12:30:00Z"},
    {"event": "CPI m/m", "currency": "USD", "time": "2026-07-15T12:30:00Z"},
    {"event": "ECB Rate Decision", "currency": "EUR", "time": "2026-07-24T11:45:00Z"},
    {"event": "ECB Rate Decision", "currency": "EUR", "time": "2026-09-11T11:45:00Z"}
  ]
}
```
⚠️ Re-implement independently — jangan copy file kalender pihak ketiga.

### Code — `src/rtrade/data/static_calendar.py`
[CODE]:
```python
"""Static last-resort economic-calendar provider (FR-CAL-03).

Zero network calls (NFR-SCALE-03). Memuat event high-impact berulang dari
config/static_calendar.json (refresh manual kuartalan). Tier terminal composite.
"""

from __future__ import annotations
from datetime import UTC, date, datetime
import hashlib, json
from pathlib import Path
import structlog
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)
_DEFAULT_PATH = Path("config/static_calendar.json")


class StaticCalendarProvider(CalendarProvider):
    def __init__(self, config_path: Path = _DEFAULT_PATH) -> None:
        self._path = config_path
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        self._version = str(raw.get("version", "unknown"))
        self._events: list[tuple[datetime, str, str]] = []
        for e in raw.get("events", []):
            t = datetime.fromisoformat(e["time"].replace("Z", "+00:00"))
            self._events.append((t, str(e["event"]), str(e["currency"])))

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        out: list[DomainEvent] = []
        now = datetime.now(UTC)
        for event_time, event_name, currency in self._events:
            d = event_time.date()
            if start <= d <= end:
                eid = hashlib.sha256(
                    f"static:{event_name}:{event_time.isoformat()}:{currency}".encode()
                ).hexdigest()[:16]
                out.append(DomainEvent(
                    event_id=eid, event=event_name, currency=currency, impact="high",
                    event_time=event_time, actual=None, forecast=None, previous=None, fetched_at=now,
                ))
        logger.info("static calendar served", start=start.isoformat(), end=end.isoformat(),
                    total=len(out), version=self._version)
        return out

    async def close(self) -> None:
        return None
```

### Verification
```bash
mypy --strict src/rtrade/data/static_calendar.py
pytest tests/unit/test_static_calendar.py -x --cov=rtrade/data/static_calendar --cov-fail-under=80
ruff check src/rtrade/data/static_calendar.py
```

### Acceptance [TEST] — `tests/unit/test_static_calendar.py`
```python
import json, tempfile
from datetime import date
from pathlib import Path
from rtrade.data.static_calendar import StaticCalendarProvider

async def test_yields_fomc_in_window():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"version": "test", "events": [
            {"event": "FOMC Rate Decision", "currency": "USD", "time": "2026-07-30T18:00:00Z"}
        ]}, f)
        path = Path(f.name)
    p = StaticCalendarProvider(path)
    events = await p.fetch_events(date(2026, 7, 29), date(2026, 7, 31))
    assert len(events) == 1
    assert events[0].impact == "high"
    assert events[0].currency == "USD"
    await p.close()
```

### Known pitfalls ⚠️
- `event_time` WAJIB ISO 8601 dengan `Z`/offset; parsing fail-fast di `__init__`.
- Static hanya `impact="high"`.

### Refs
FR-CAL-03, NFR-SCALE-03, G-01, ADR-A02.

---

## P0-5 — CompositeCalendarProvider 🔴

### Why
Single source rapuh terhadap gate fail-CLOSE. Composite primary→secondary→static + per-source last_success + alert tiap transisi.

### Code — `src/rtrade/data/composite_calendar.py`
[CODE]:
```python
"""Composite economic-calendar provider (FR-CAL-02).

Tries sources in configured order. Records per-source health. Emits alert
on each fallback transition + total staleness. Fail-CLOSE ditangani di gate.
"""

from __future__ import annotations
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
import structlog
from rtrade.core.errors import ProviderError
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)


@dataclass
class CalendarSourceHealth:
    name: str
    last_success: datetime | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    last_attempt: datetime | None = None


AlertCallback = Callable[[str], Awaitable[None]]


class CompositeCalendarProvider(CalendarProvider):
    def __init__(self, sources: list[CalendarProvider], *, names: list[str],
                 alert_callback: AlertCallback | None = None) -> None:
        if len(sources) != len(names):
            raise ValueError("sources and names length must match")
        self._sources = list(zip(names, sources, strict=True))
        self._health: dict[str, CalendarSourceHealth] = {n: CalendarSourceHealth(name=n) for n in names}
        self._alert = alert_callback

    async def _emit_alert(self, message: str) -> None:
        if self._alert is not None:
            try: await self._alert(message)
            except Exception as exc:
                logger.warning("calendar alert callback failed", error=str(exc))

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        last_tier: str | None = None
        for name, provider in self._sources:
            health = self._health[name]
            health.last_attempt = datetime.now(UTC)
            try:
                events = await provider.fetch_events(start, end)
            except Exception as exc:
                health.last_error = str(exc)
                health.consecutive_failures += 1
                logger.warning("calendar source failed", source=name, error=str(exc),
                               consecutive=health.consecutive_failures)
                if last_tier is not None:
                    await self._emit_alert(f"⚠️ Calendar fallback: {last_tier} gagal → coba {name}")
                last_tier = name
                continue
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.last_error = None
            if last_tier is not None:
                await self._emit_alert(f"⚠️ Calendar fallback aktif: {last_tier} → {name} (recovered)")
            return events
        await self._emit_alert("🚨 CALENDAR: SEMUA sumber gagal (total staleness)")
        raise ProviderError("all calendar sources unavailable")

    def health_snapshot(self) -> dict[str, CalendarSourceHealth]:
        return dict(self._health)

    def freshest_last_success(self) -> datetime | None:
        times = [h.last_success for h in self._health.values() if h.last_success]
        return max(times) if times else None

    def active_tier(self) -> str | None:
        best: tuple[datetime, str] | None = None
        for h in self._health.values():
            if h.last_success is not None and (best is None or h.last_success > best[0]):
                best = (h.last_success, h.name)
        return best[1] if best else None

    async def close(self) -> None:
        for _, provider in self._sources:
            try: await provider.close()
            except Exception as exc:
                logger.warning("calendar provider close failed", error=str(exc))
```

### Verification
```bash
mypy --strict src/rtrade/data/composite_calendar.py
pytest tests/integration/test_composite_calendar.py -x --cov=rtrade/data/composite_calendar --cov-fail-under=80
```

### Acceptance [TEST] — `tests/integration/test_composite_calendar.py` (QA-INT-01)
```python
from datetime import date, datetime, timezone
import pytest
from rtrade.core.errors import ProviderError
from rtrade.data.base import CalendarProvider, EconomicEvent as DomainEvent
from rtrade.data.composite_calendar import CompositeCalendarProvider

async def _noop(): return None

class _Stub(CalendarProvider):
    def __init__(self, events=None, exc=None):
        self._events = events or []; self._exc = exc
    async def fetch_events(self, start, end):
        if self._exc: raise self._exc
        return self._events
    async def close(self): pass

async def test_primary_success_no_alert():
    alerted = []
    ev = DomainEvent("id","FOMC","USD","high", datetime(2026,6,18,tzinfo=timezone.utc))
    primary = _Stub(events=[ev])
    comp = CompositeCalendarProvider([primary], names=["investing"],
        alert_callback=lambda m: (alerted.append(m), _noop())[1])
    events = await comp.fetch_events(date(2026,6,17), date(2026,6,19))
    assert len(events) == 1 and alerted == []

async def test_primary_fails_fallback_to_static_with_alert():
    alerted = []
    investing = _Stub(exc=RuntimeError("boom"))
    static = _Stub(events=[DomainEvent("id","FOMC","USD","high", datetime(2026,6,18,tzinfo=timezone.utc))])
    comp = CompositeCalendarProvider([investing, static], names=["investing","static_high_impact"],
        alert_callback=lambda m: (alerted.append(m), _noop())[1])
    events = await comp.fetch_events(date(2026,6,17), date(2026,6,19))
    assert len(events) == 1
    assert any("fallback" in a.lower() for a in alerted)

async def test_all_fail_raises_and_alerts():
    alerted = []
    comp = CompositeCalendarProvider([_Stub(exc=RuntimeError("a")),_Stub(exc=RuntimeError("b"))],
        names=["investing","static_high_impact"],
        alert_callback=lambda m: (alerted.append(m), _noop())[1])
    with pytest.raises(ProviderError):
        await comp.fetch_events(date(2026,6,17), date(2026,6,19))
    assert any("SEMUA" in a for a in alerted)
```

### Known pitfalls ⚠️
- `zip(..., strict=True)` Python 3.10+ (OK py3.12).
- Alert callback failure TIDAK boleh crash fetch — wrap try/except.

### Refs
FR-CAL-02, FR-CAL-05, NFR-REL-01, G-02, ADR-A03.

---

## P0-8 — CalendarSourceHealth table + repo 🔴

### Why
`latest_fetch_ts` (MAX `fetched_at`) bug: sync over empty window tidak ubah timestamp → kalender kelihatan fresh padahal sync gagal.

### Code — edit `src/rtrade/persistence/models.py`
[CODE] tambah setelah `EconomicEvent`:
```python
class CalendarSourceHealth(Base):
    """Per-source metadata kalender (FR-CAL-04). last_success menggantikan
    bug MAX(fetched_at) yang tidak maju saat sync over empty window."""

    __tablename__ = "calendar_source_health"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    last_success: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )
```

### Code — Alembic migration
```bash
uv run alembic revision -m "add calendar_source_health table"
```
Edit generated file:
```python
from alembic import op
import sqlalchemy as sa

revision = "<auto>"
down_revision = "<previous>"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "calendar_source_health",
        sa.Column("source", sa.Text(), primary_key=True),
        sa.Column("last_success", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("calendar_source_health")
```

### Code — edit `src/rtrade/persistence/repositories.py`
[CODE] tambah:
```python
class CalendarSourceHealthRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, source: str, *, last_success: datetime | None,
                     last_error: str | None, consecutive_failures: int,
                     last_attempt: datetime | None) -> None:
        existing = await self._session.get(CalendarSourceHealth, source)
        if existing is None:
            existing = CalendarSourceHealth(source=source)
            self._session.add(existing)
        existing.last_success = last_success
        existing.last_error = last_error
        existing.consecutive_failures = consecutive_failures
        existing.last_attempt = last_attempt

    async def all(self) -> list[CalendarSourceHealth]:
        result = await self._session.execute(select(CalendarSourceHealth))
        return list(result.scalars().all())

    async def freshest_success(self) -> datetime | None:
        result = await self._session.execute(select(func.max(CalendarSourceHealth.last_success)))
        return result.scalar_one_or_none()
```
Import `CalendarSourceHealth` dari `rtrade.persistence.models`.

### Verification
```bash
uv run alembic upgrade head
mypy --strict src/rtrade/persistence/
pytest tests/integration/test_calendar_health_repo.py -x
```

### Acceptance [TEST] — `tests/integration/test_calendar_health_repo.py`
```python
from datetime import datetime, timezone
async def test_upsert_and_freshest(db_session):
    from rtrade.persistence.repositories import CalendarSourceHealthRepo
    repo = CalendarSourceHealthRepo(db_session)
    await repo.upsert("investing", last_success=datetime(2026,6,19,12,tzinfo=timezone.utc),
                      last_error=None, consecutive_failures=0, last_attempt=datetime(2026,6,19,12,tzinfo=timezone.utc))
    await repo.upsert("static_high_impact", last_success=datetime(2026,6,19,10,tzinfo=timezone.utc),
                      last_error=None, consecutive_failures=0, last_attempt=datetime(2026,6,19,10,tzinfo=timezone.utc))
    await db_session.commit()
    freshest = await repo.freshest_success()
    assert freshest is not None
    assert len(await repo.all()) == 2
```

### Refs
FR-CAL-04, G-02, NFR-REL-02.

---

## P0-6 — Wire composite ke `sync_calendar` + verify GR-07b 🔴 THE ACCEPTANCE

### Why
Tes paling penting di seluruh P0. Bila hijau, non-crypto coverage restored.

### Code — edit `src/rtrade/pipeline/scan.py`
[CODE] tambah factory + rewrite `sync_calendar`:
```python
def _make_calendar_provider(name: str, cfg: AppConfig, limiter: RateLimiter) -> CalendarProvider:
    """Factory source-agnostic berdasarkan nama config (FR-CAL-07)."""
    if name == "investing":
        from rtrade.data.investing_calendar import InvestingCalendarProvider
        return InvestingCalendarProvider()
    if name == "static_high_impact":
        from rtrade.data.static_calendar import StaticCalendarProvider
        return StaticCalendarProvider()
    if name == "finnhub":
        if not cfg.secrets.finnhub_api_key:
            raise ConfigError("finnhub source enabled but FINNHUB_API_KEY empty")
        return FinnhubCalendarProvider(cfg.secrets.finnhub_api_key, limiter)
    if name == "nasdaq":
        raise NotImplementedError("nasdaq provider lands in P1-1")
    raise ConfigError(f"unknown calendar source: {name!r}")


async def _calendar_alert(message: str) -> None:
    """Inline alert callback untuk composite (re-point ke AlertManager di P2-5)."""
    from rtrade.scheduler.jobs import _send_failure_alert
    await _send_failure_alert(message)


async def sync_calendar(*, config: AppConfig | None = None,
                        config_dir: Path | str = Path("config"),
                        env_file: Path | str | None = Path(".env")) -> int:
    cfg = config or AppConfig.load(config_dir=config_dir, env_file=env_file)
    redis_client = _get_redis(cfg)
    limiter = RateLimiter(redis_client)
    engine = _get_engine(cfg)
    session_factory = create_session_factory(engine)

    cal_cfg = cfg.settings.calendar
    enabled = [s for s in cal_cfg.sources if s.enabled]
    if not enabled:
        raise ConfigError("no enabled calendar sources")

    providers: list[CalendarProvider] = []
    names: list[str] = []
    for src in enabled:
        try:
            providers.append(_make_calendar_provider(src.name, cfg, limiter))
            names.append(src.name)
        except NotImplementedError:
            logger.warning("calendar source not yet implemented, skipping", source=src.name)

    composite = CompositeCalendarProvider(providers, names=names, alert_callback=_calendar_alert)
    try:
        today = datetime.now(UTC).date()
        start = today - timedelta(days=cal_cfg.sync_lookback_days)
        end = today + timedelta(days=cal_cfg.sync_lookforward_days)
        events = await composite.fetch_events(start, end)
        orm_events = [EconomicEvent(
            id=e.event_id, event=e.event, currency=e.currency, impact=e.impact,
            event_time=e.event_time, actual=e.actual, forecast=e.forecast,
            previous=e.previous, fetched_at=e.fetched_at,
        ) for e in events]
        async with session_factory() as session:
            count = await EventRepo(session).upsert_many(orm_events)
            health_repo = CalendarSourceHealthRepo(session)
            for name, h in composite.health_snapshot().items():
                await health_repo.upsert(name, last_success=h.last_success,
                    last_error=h.last_error, consecutive_failures=h.consecutive_failures,
                    last_attempt=h.last_attempt)
            await session.commit()
            return count
    finally:
        await composite.close()
        # engine & redis process-scoped — JANGAN dispose (P0 fix #6)
```

### Code — edit `run_scan` freshness derivation (`scan.py:341`)
[CODE] ganti `calendar_ts = await EventRepo(session).latest_fetch_ts()`:
```python
# FR-CAL-04: pakai freshest source health (lebih akurat dari MAX(fetched_at)).
calendar_ts = await CalendarSourceHealthRepo(session).freshest_success()
if calendar_ts is None:
    calendar_ts = await EventRepo(session).latest_fetch_ts()  # backwards-compat
```

### Verification
```bash
mypy --strict src/rtrade/pipeline/scan.py
pytest tests/integration/test_scan_non_crypto_resumes.py -x
psql -c "SELECT source, last_success, consecutive_failures FROM calendar_source_health;"
psql -c "SELECT COUNT(*) FROM economic_events WHERE event_time >= NOW() - INTERVAL '7 days';"
```

### Acceptance [TEST] — `tests/integration/test_scan_non_crypto_resumes.py` (QA-INT-02, THE P0 TEST)
```python
from freezegun import freeze_time

@freeze_time("2026-06-19T12:00:00Z")
async def test_fx_signal_completes_gate_path_with_fresh_calendar(
    seeded_db, mock_twelvedata, mock_investing_calendar
):
    await seed_fresh_calendar_window(seeded_db)
    result = await run_scan("EURUSD", "1h", config=test_config, deliver=False)
    assert result.status in {"published", "rejected", "duplicate", "insufficient_data", "abstain_warmup"}
    if result.status == "rejected":
        assert not any("GR-07" in f for f in result.failures), \
            f"GR-07b masih reject padahal kalender fresh: {result.failures}"

@freeze_time("2026-06-19T12:00:00Z")
async def test_fx_signal_rejected_by_gr07b_when_stale(seeded_db, mock_twelvedata):
    await clear_calendar(seeded_db)
    result = await run_scan("EURUSD", "1h", config=test_config, deliver=False)
    if result.status == "rejected":
        assert any("GR-07" in f for f in result.failures)
```

### Known pitfalls ⚠️
- Crypto (BTC/ETH) tetap exempt dari `calendar_stale` (`instrument.market != Market.CRYPTO`). Jangan ubah.
- `_get_engine`/`_get_redis` singletons — JANGAN dispose di finally.
- Test WAJIB pakai DB seeded + provider mock, bukan live.

### Refs
FR-CAL-08, FR-CAL-04, G-01, DEF-REQ-01, ADR-A02, QA-INT-02.

---

## P0-7 — Startup warning no calendar source (FR-SCH-07) 🔴

### Code — edit `src/rtrade/scheduler/main.py` `run_worker()`
[CODE]:
```python
def _has_calendar_source(cfg: AppConfig) -> bool:
    """True bila setidaknya satu source kalender aktif dan buildable."""
    for src in cfg.settings.calendar.sources:
        if not src.enabled: continue
        if src.name == "finnhub" and not cfg.secrets.finnhub_api_key: continue
        if src.name in {"investing", "static_high_impact", "nasdaq", "trading_economics"}:
            return True
    return False

# di run_worker(), setelah configure_logging() sebelum selftest:
cfg = AppConfig.load()
has_non_crypto = any(i.market.value != "crypto" for i in cfg.instruments)
if has_non_crypto and not _has_calendar_source(cfg):
    if cfg.settings.calendar.fail_open_when_stale:
        logger.warning("no calendar source; fail-open active — non-crypto akan trade buta terhadap berita")
    else:
        logger.critical("no calendar source; GR-07b akan REJECT SEMUA signal non-crypto")
```

### Verification
```bash
mypy --strict src/rtrade/scheduler/main.py
pytest tests/unit/test_worker_startup_warnings.py -x
```

### Refs
FR-SCH-07, G-01, NFR-REL-03.

---

## P0-EXIT — Exit Gate Verification 🔴

**Jangan mulai P1 sebelum SEMUA ini hijau dengan output pasted.**

### Commands
```bash
ruff check src/ tests/ && ruff format --check src/ tests/
mypy --strict src/
pytest tests/unit tests/property -x
pytest tests/integration -x
pytest --cov=rtrade/data --cov=rtrade/pipeline --cov=rtrade/scheduler --cov=rtrade/persistence --cov=rtrade/core \
       --cov-fail-under=80 src/rtrade/data src/rtrade/pipeline
pytest tests/unit -k selftest -x
# Manual smoke (staging): jalankan worker, tunggu sync_calendar, cek economic_events terisi.
```

### Exit Criteria (ALL must hold)
- ✅ Non-crypto scans produce fresh calendar window ≥95% cycles.
- ✅ GR-07b rejections non-crypto <5% (dari ~100%); ≥1 FX/XAU signal completes 13-gate path (QA-INT-02 green).
- ✅ ADR-A10 merged (P0-1).
- ✅ Owner calendar-source decision recorded.
- ✅ `make ci` hijau. Coverage `data/` calendar ≥80%, `pipeline/` ≥60%, `persistence/` ≥60%.

**STOP. Paste output. Tunggu konfirmasi sebelum P1.**

### Refs
DEF-REQ-01, SM-01, FR-CAL-08, FR-CAL-21, ADR-A02, QA-INT-02.

---

# PHASE P1 — RELIABILITY + DORMANT SAFETY + GO-LIVE GATE 🟠

> **Goal:** buat data layer resilient, tutup gate path yang dormant, dan buat statistical go-live gate executable. P0 membuktikan satu source jalan; P1 mengeras dependency dan menyalakan mesin safety/validation yang laten.

## Item eksekusi P1 (urut)

| ID | Item | Prioritas |
|---|---|---|
| P1-1 | Provider secondary (Nasdaq/TradingEconomics) | 🟠 |
| P1-2 | Health telemetry → `/health` endpoint | 🟠 |
| P1-3 | Wire GR-09/10/11 args through `run_gate()` (post-LLM) | 🟠 CRITICAL |
| P1-4 | Extend selftest ke 13 gate (THE NET) | 🟠 |
| P1-5 | GR-06 fail-CLOSE bila live quote hilang | 🟠 |
| P1-6 | `rtrade.cli.backtest` go-live gate runner | 🟠 HIGH |
| P1-7 | Cold-start warmup guarantee | 🟠 (tarik maju P2) |
| P1-EXIT | Verifikasi exit gate | 🟠 |

---

## P1-1 — Provider secondary (FR-CAL-02) 🟠

### Why
Composite butuh source kedua tipe berbeda agar satu ToS/outage tidak men-gebok gate.

### What changes
- File baru `src/rtrade/data/nasdaq_calendar.py` (atau `trading_economics_calendar.py`) — mirror pola P0-3 (httpx, transient-only retry, sanitize, deterministic event_id, 4xx/5xx → ProviderError, 429 → RateLimitExceeded).
- Tambah ke factory `_make_calendar_provider` (`scan.py`) dan `config/settings.yaml calendar.sources`.

### Code — `config/settings.yaml`
[CODE] tambahkan nasdaq sebagai secondary (sebelum static):
```yaml
calendar:
  ...
  sources:
    - { name: investing, enabled: true }
    - { name: nasdaq, enabled: true }      # secondary
    - { name: static_high_impact, enabled: true }
```

### Code — edit `scan.py _make_calendar_provider`
[CODE] ganti branch `nasdaq`:
```python
    if name == "nasdaq":
        from rtrade.data.nasdaq_calendar import NasdaqCalendarProvider
        return NasdaqCalendarProvider()
```

### Code — `src/rtrade/data/nasdaq_calendar.py`
[CODE] (struktur identik P0-3, beda `_BASE_URL`/endpoint/param/parse — sesuaikan dengan Nasdaq Data Link API docs):
```python
"""Nasdaq Data Link economic-calendar provider (independent re-implementation).

Secondary source tipe berbeda dari Investing (FR-CAL-02). Keyed API (bila perlu
key, tambah NDAQ_API_KEY ke Secrets + config). httpx + transient-only retry.
"""
from __future__ import annotations
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
import hashlib, os
import httpx, structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from rtrade.core.errors import ProviderError, RateLimitExceeded
from rtrade.core.text_sanitize import sanitize_event_text
from rtrade.data.base import CalendarProvider
from rtrade.data.base import EconomicEvent as DomainEvent

logger = structlog.get_logger(__name__)
_BASE_URL = "https://data.nasdaq.com/api/v3"  # confirm via docs

# ... (mirror _to_currency / _normalize_impact / _event_id / _safe_decimal dari P0-3,
#      gunakan prefix "nasdaq:" di _event_id)

class NasdaqCalendarProvider(CalendarProvider):
    def __init__(self, *, api_key: str | None = None, http_timeout: float = 15.0) -> None:
        self._api_key = api_key or os.environ.get("NDAQ_API_KEY", "")
        self._http = httpx.AsyncClient(base_url=_BASE_URL, timeout=http_timeout,
                                       headers={"Accept": "application/json",
                                                "User-Agent": "RobilTrade/0.1"})

    @retry(retry=retry_if_exception_type(httpx.TransportError),
           wait=wait_exponential(multiplier=1, min=1, max=8),
           stop_after_attempt=3), reraise=True)
    async def _get(self, path, params): return await self._http.get(path, params=params)

    async def fetch_events(self, start: date, end: date) -> list[DomainEvent]:
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        if self._api_key: params["api_key"] = self._api_key
        try:
            resp = await self._get("/datasets/FRED/...", params)  # confirm dataset code
        except httpx.HTTPError as exc:
            raise ProviderError(f"Nasdaq HTTP error: {exc}") from exc
        if resp.status_code == 429: raise RateLimitExceeded("Nasdaq 429")
        if resp.status_code in (401, 403): raise ProviderError(f"Nasdaq {resp.status_code}")
        if resp.status_code >= 400:
            raise ProviderError(f"Nasdaq HTTP {resp.status_code}: {resp.text[:200]}")
        # ... parse rows → DomainEvent (mirror Investing parsing pattern)
        ...

    async def close(self): await self._http.aclose()
```

### Verification
```bash
mypy --strict src/rtrade/data/nasdaq_calendar.py
pytest tests/integration/test_composite_calendar.py -x  # extend: investing+nasdaq+static
```

### Acceptance [TEST] — extend `tests/integration/test_composite_calendar.py`
```python
async def test_composite_primary_healthy_uses_first():
    # investing+nasdaq+static semua sehat → pakai investing (first)
async def test_composite_primary_fails_uses_secondary():
    # investing raise → pakai nasdaq + alert fallback
async def test_composite_primary_secondary_fail_use_static():
    # investing+nasdaq raise → pakai static + 2 alert
```

### Refs
FR-CAL-02, NFR-REL-01, G-02, ADR-A03.

---

## P1-2 — Health telemetry → `/health` (FR-CAL-04, FR-OBS-03) 🟠

### Why
Operator butuh lihat status kalender per-source di health endpoint.

### Code — edit `src/rtrade/delivery/api/routes.py`
[CODE] extend `/health`:
```python
from rtrade.persistence.repositories import CalendarSourceHealthRepo

@router.get("/health")
async def health(session: AsyncSession = Depends(get_session),
                 _: None = Depends(verify_bearer)) -> dict:
    checker = HealthChecker(...)
    base = await checker.run_all()
    # Tambah calendar sources
    health_rows = await CalendarSourceHealthRepo(session).all()
    base["calendar_sources"] = [
        {"source": h.source, "last_success_age_s": (
            (datetime.now(UTC) - h.last_success).total_seconds() if h.last_success else None
        ), "consecutive_failures": h.consecutive_failures,
         "last_error": h.last_error}
        for h in health_rows
    ]
    # Aggregate worst-of: UNHEALTHY > DEGRADED > HEALTHY
    cfg = AppConfig.load()
    stale_after = timedelta(hours=cfg.settings.calendar.stale_after_hours)
    has_non_crypto = any(i.market.value != "crypto" for i in cfg.instruments)
    all_stale = all(
        (h.last_success is None or datetime.now(UTC) - h.last_success > stale_after)
        for h in health_rows
    ) if health_rows else True
    if has_non_crypto and all_stale and not cfg.settings.calendar.fail_open_when_stale:
        base["status"] = "UNHEALTHY"
        base["calendar"] = "all sources stale — GR-07b rejecting non-crypto"
    elif any(h.consecutive_failures > 0 for h in health_rows):
        base["status"] = max(base.get("status", "HEALTHY"), "DEGRADED")
    return base
```
⚠️ Sesuaikan signature `HealthChecker.run_all()` & `verify_bearer`/`get_session` yang sebenarnya (re-read routes.py).

### Verification
```bash
pytest tests/integration/test_health_endpoint.py -x
curl -s localhost:8000/health -H "Authorization: Bearer $TOKEN" | jq .calendar_sources
```

### Acceptance
- `/health` payload berisi `calendar_sources` per-source.
- Forced outage → UNHEALTHY dalam satu sync cycle.

### Refs
FR-CAL-04, FR-OBS-03, NFR-OBS-01.

---

## P1-3 — Wire GR-09/10/11 args through `run_gate()` (G-03) 🟠 CRITICAL

### Why
Tiga gate (confidence floor, no-LLM-number-mutation, citations) dormant di production karena argumen tidak pernah dipass. Wajib sebelum `llm.enabled=true`.

### Context
Hanya ada **satu** call `run_gate(...)` di pipeline, di `scan.py:908-927` (`_run_strategies`). OMITS `confidence`, `original_candidate`, `sources`, `pack_source_ids` → GR-09/10/11 skip (gate.py guard `if param is not None`). LLM jalan *after* gate; `pres.confidence`/`pres.sources` computed di `scan.py:1058/1061` *after* gate pass.

### Strategy: post-LLM gate (rekomendasi)
1. **Keep** existing deterministic `run_gate` di tempatnya (enforce GR-01..08/12/13).
2. **Add** post-LLM `run_gate` saat `cfg.settings.llm.enabled` True, pass 4 args:

### Code — edit `scan.py` LLM section (sekitar `:971-1062`)
[CODE] setelah `pres = await run_llm_pipeline(...)` dan sebelum persist:
```python
if cfg.settings.llm.enabled:
    # ... existing: build pack, run cascade → pres ...
    pack_source_ids = set(pack.source_ids) if pack else set()
    post_llm_gate = run_gate(
        candidate,
        confidence=float(pres.confidence),
        confidence_min=cfg.settings.signal.confidence_min,
        original_candidate=candidate,  # frozen pre-LLM; GR-10 vacuous today, durable vs future
        sources=pres.sources or ["deterministic_pipeline"],
        pack_source_ids=pack_source_ids,
        # re-pass P1 args agar full 13-gate run post-LLM:
        latest_candle_ts=candidate.bar_ts,
        timeframe=candidate.timeframe,
        staleness_factor=cfg.settings.signal.candle_staleness_factor,
        live_price=live_price,
        price_drift_max_pct=cfg.settings.signal.price_drift_max_pct,
        now=now,
        events=event_dicts,
        related_currencies=instrument.related_currencies,
        news_blackout_before_min=cfg.settings.risk.news_blackout_before_min,
        news_blackout_after_min=cfg.settings.risk.news_blackout_after_min,
        calendar_stale=calendar_stale,
        regime=regime.regime,
        required_regime=strategy.required_regime,
        signals_today=signals_today,
        max_signals_per_day=cfg.settings.signal.max_signals_per_day_per_instrument,
        paper_outcomes=paper_outcomes,
        expectancy_window=cfg.settings.risk.expectancy_guard_window,
    )
    await audit_repo.add(stage=AuditStage.GATE.value, ok=post_llm_gate.passed,
                         signal_id=candidate.candidate_id,
                         detail={"phase": "post_llm",
                                 "failures": [f"{f.gate_id}: {f.reason}" for f in post_llm_gate.failures]})
    if not post_llm_gate.passed:
        # persist REJECTED + return (mirror deterministic reject path)
        await session_repo.add(_signal_model(candidate, instrument_id,
            status=SignalStatus.REJECTED, confidence=Decimal("0"),
            payload={"candidate": candidate.model_dump(mode="json"),
                     "gate_post_llm": post_llm_gate.model_dump(mode="json")}))
        return ScanResult(symbol=instrument.symbol, timeframe=candidate.timeframe.value,
                          status="rejected", signal_id=candidate.candidate_id,
                          failures=[f"{f.gate_id}: {f.reason}" for f in post_llm_gate.failures])
```
⚠️ **Re-read `scan.py:971-1062`** untuk context variabel sebenarnya (`pack`, `pres`, dll.). `llm.enabled` STAYS false di CI — test wiring via stub.

### Verification
```bash
mypy --strict src/rtrade/pipeline/scan.py
pytest tests/unit/test_scan_gate_wiring.py -x
```

### Acceptance [TEST] — `tests/unit/test_scan_gate_wiring.py` (QA-GATE-02)
```python
from unittest.mock import AsyncMock, patch

async def test_post_llm_gate_receives_all_four_args(monkeypatch):
    """llm.enabled=True (stubbed) → run_gate dipass confidence/original/sources/pack_source_ids."""
    captured = {}
    real_run_gate = run_gate
    def spy(candidate, **kw):
        captured.update(kw)
        return real_run_gate(candidate, **kw)
    monkeypatch.setattr("rtrade.pipeline.scan.run_gate", spy)
    # ... config dgn llm.enabled=True, LLM pipeline stubbed to return pres ...
    await run_scan(...)
    assert captured.get("confidence") is not None
    assert captured.get("original_candidate") is not None
    assert captured.get("sources") is not None
    assert captured.get("pack_source_ids") is not None

async def test_llm_disabled_no_post_llm_gate(monkeypatch):
    """llm.enabled=False → tidak ada second run_gate call."""
    ...

async def test_gr10_mutation_rejected():
    """LLM return candidate mutated level → GR-10 reject."""
    ...
```

### Known pitfalls ⚠️
- Jangan pindah gate deterministic post-LLM — itu artinya LLM-disabled scan tak tergate.
- `original_candidate=candidate` vacuous hari ini (LLM tak mutate frozen candidate) tapi durable.
- Stub LLM pipeline di test, JANGAN enable `llm.enabled` di CI config.

### Refs
G-03, FR-GR-04/05/06/07, FR-GR-09/10/11, DEF-REQ-03, QA-GATE-02, ADR-A05.

---

## P1-4 — Extend selftest ke 13 gate (G-10) 🟠 THE NET

### Why
Selftest hanya cover GR-09/10/12 + 1 regression. 10 dari 13 gate tak teruji saat boot. Worker `SystemExit` jadi tidak benar-benar melindungi.

### Context
`guardrails/selftest.py`: `_make_candidate(**overrides)` (`:16-40`) builds known-good XAUUSD BUY (entry=2000, SL=1990, TP=2020, atr=5, confluence=70, risk_pct=1.0, position_size=0.5). `run_guardrail_selftest() -> list[str]` (`:43`) return list of failure messages (empty = healthy).

### What changes
Extend `run_guardrail_selftest()` dengan known-bad candidate per gate.

### Code — edit `src/rtrade/guardrails/selftest.py`
[CODE] tambah per-gate bad fixtures (lihat tabel PRD §17.4):
```python
def run_guardrail_selftest() -> list[str]:
    problems: list[str] = []

    def expect_fail(gate_id: str, candidate=None, **kw) -> None:
        if candidate is None:
            candidate = _make_candidate()
        result = run_gate(candidate, **kw)
        if result.passed or not any(f.gate_id == gate_id for f in result.failures):
            problems.append(f"{gate_id} did not reject as expected (kw={kw})")

    def expect_pass(candidate=None, **kw) -> None:
        if candidate is None: candidate = _make_candidate()
        result = run_gate(candidate, **kw)
        if not result.passed:
            problems.append(f"good candidate was rejected: {[f.gate_id for f in result.failures]}")

    good = _make_candidate()

    # GR-02 direction: BUY dengan SL>entry (invalid) — tapi Pydantic tolak saat konstruksi.
    # Karena candidate frozen + validated, test lewat schema raise:
    try:
        _make_candidate(stop_loss=2010.0)  # SL > entry → invalid BUY
        problems.append("GR-02: candidate with SL>=entry should fail construction")
    except Exception:
        pass  # expected

    # GR-03 RR floor: TP terlalu dekat
    expect_fail("GR-03", _make_candidate(take_profit=2005.0))  # RR = 5/10 = 0.5

    # GR-04 SL band: SL di luar [0.5, 3.0]×ATR
    expect_fail("GR-04", _make_candidate(stop_loss=1900.0))  # 100/5 = 20x ATR

    # GR-05 risk cap
    expect_fail("GR-05", _make_candidate(risk_pct=2.5))  # > 2.0 (perlu bypass validator atau test di gate level)

    # GR-06 freshness: candle stale
    from datetime import datetime, timedelta, UTC
    old_ts = datetime.now(UTC) - timedelta(hours=5)  # > 2× H1
    expect_fail("GR-06", latest_candle_ts=old_ts, timeframe=Timeframe.H1, staleness_factor=2.0)

    # GR-06 fail-close: required live quote missing (G-09)
    expect_fail("GR-06", live_quote_required=True, live_price=None)

    # GR-07 news blackout
    expect_fail("GR-07", events=[{"event_time": datetime.now(UTC), "impact": "high", "currency": "USD"}],
                related_currencies=["USD"], news_blackout_before_min=30, news_blackout_after_min=15,
                now=datetime.now(UTC))

    # GR-07b calendar stale
    expect_fail("GR-07", calendar_stale=True)  # note: gate_id GR-07 (label) per gate.py:155

    # GR-08 regime CRISIS
    expect_fail("GR-08", regime=Regime.CRISIS, required_regime=Regime.TREND)

    # GR-08 regime mismatch
    expect_fail("GR-08", regime=Regime.RANGE, required_regime=Regime.TREND)

    # GR-09 confidence floor (existing)
    expect_fail("GR-09", confidence=0.30, confidence_min=0.55)

    # GR-10 mutation (existing)
    original = _make_candidate()
    mutated = _make_candidate(entry_limit=2000.1)  # berbeda
    expect_fail("GR-10", candidate=mutated, original_candidate=original)

    # GR-11 citations: empty sources
    expect_fail("GR-11", sources=[])
    # GR-11 citations: invalid source id
    expect_fail("GR-11", sources=["fake_id"], pack_source_ids={"real_id"})

    # GR-12 rate cap (existing)
    expect_fail("GR-12", signals_today=5, max_signals_per_day=3)

    # GR-13 expectancy: negative rolling outcomes
    expect_fail("GR-13", paper_outcomes=[-1.0]*30, expectancy_window=30)

    # Regression: good candidate must PASS
    expect_pass(good)

    return problems
```
⚠️ `_make_candidate` override field harus konsisten dgn frozen Pydantic validator. Untuk GR-05 (risk_pct>2), validator Pydantic akan tolak konstruksi — test di level gate dengan `model_construct` bypass HANYA untuk selftest ini, atau skip & catat bahwa validator sudah enforce. Re-read `signals/schemas.py` SignalCandidate untuk field & validator sebenarnya.

### Verification
```bash
pytest tests/unit/test_guardrail_selftest.py -x
# Verifikasi worker SystemExit saat gate broken:
python -c "
from rtrade.guardrails.gate import run_gate
from rtrade.guardrails import selftest
# Inject broken gate via monkeypatch ...
"
```

### Acceptance [TEST] — `tests/unit/test_guardrail_selftest.py` (QA-GATE-01)
```python
def test_selftest_healthy_returns_empty():
    assert selftest.run_guardrail_selftest() == []

def test_selftest_catches_broken_gate(monkeypatch):
    """Inject GR-03 threshold = 0 → selftest detect bad gate."""
    real = run_gate
    def broken(candidate, **kw):
        result = real(candidate, **kw)
        # hapus GR-03 failures simulasi gate rusak
        result.failures = [f for f in result.failures if f.gate_id != "GR-03"]
        result.passed = len(result.failures) == 0
        return result
    monkeypatch.setattr("rtrade.guardrails.selftest.run_gate", broken)
    problems = selftest.run_guardrail_selftest()
    assert any("GR-03" in p for p in problems)

def test_worker_refuses_start_on_selftest_fail(monkeypatch):
    """main.run_worker SystemExit(1) saat selftest fail."""
    ...
```

### Known pitfalls ⚠️
- GR-13 auto-disable (strategy_state) adalah side-effect DB — selftest hanya test gate reject, bukan disable (itu test integration terpisah).
- GR-07b di-label `GR-07` di gate.py:155 — assertion selftest pakai `GR-07` (atau fix label jadi `GR-07b`).

### Refs
G-10, FR-GR-08, FR-SELFTEST-01/02/03, DEF-REQ-04, QA-GATE-01, FR-SCH-01.

---

## P1-5 — GR-06 fail-CLOSE bila live quote hilang (G-09) 🟠

### Why
Saat `live_price=None` (quote fetch gagal), gate drift check skip → fail-OPEN. Inkonsisten dgn desain fail-CLOSE.

### Code — edit `src/rtrade/guardrails/gate.py` `run_gate` signature + GR-06 block
[CODE] tambah param + logic:
```python
def run_gate(
    candidate: SignalCandidate,
    *,
    # ... existing params ...
    live_price: float | None = None,
    live_quote_required: bool = False,   # NEW (G-09)
    price_drift_max_pct: float = 0.5,
    # ...
):
    # ... GR-06 section:
    # --- GR-06: Freshness + price drift (fail-CLOSE on missing required quote, G-09) ---
    if live_quote_required and live_price is None:
        failures.append(GateFailure(
            gate_id="GR-06",
            reason="required live quote unavailable — fail-closed (abstain)",
        ))
    elif live_price is not None and price_drift_max_pct > 0:
        drift_pct = abs(live_price - e) / e * 100
        if drift_pct > price_drift_max_pct:
            failures.append(GateFailure(
                gate_id="GR-06",
                reason=f"price drift {drift_pct:.2f}% > {price_drift_max_pct}% max",
            ))
    # ... keep candle freshness check ...
```

### Code — edit `scan.py` run_gate call site (`:908-927`)
[CODE] tambah `live_quote_required=True`:
```python
gate = run_gate(
    candidate,
    ...
    live_price=live_price,
    live_quote_required=True,   # NEW: fail-CLOSE bila quote hilang
    ...
)
```
⚠️ FX/metals & crypto keduanya butuh quote (drift matter untuk semua). FX no-spread (`spread=None`) tetap graceful-degrade — itu input terpisah, tidak ada di gate.

### Verification
```bash
pytest tests/unit/test_gate_gr06.py -x
pytest tests/integration -k gr06 -x
```

### Acceptance [TEST] — `tests/unit/test_gate_gr06.py` (QA-GATE-03)
```python
def test_gr06_fails_when_required_quote_missing():
    cand = _make_candidate()
    result = run_gate(cand, live_quote_required=True, live_price=None)
    assert not result.passed
    assert any(f.gate_id == "GR-06" for f in result.failures)

def test_gr06_drift_check_when_quote_present():
    cand = _make_candidate()
    result = run_gate(cand, live_quote_required=True, live_price=2000.0)  # no drift
    assert all(f.gate_id != "GR-06" for f in result.failures) or result.passed

def test_fx_no_spread_degrade_ok():
    """FX dgn quote valid tapi spread=None → tetap pass gate (spread bukan input gate)."""
    ...
```

### Refs
G-09, FR-GR-03, FR-DATA-13, NFR-REL-05, QA-GATE-03.

---

## P1-6 — `rtrade.cli.backtest` go-live gate runner (G-04) 🟠 HIGH

### Why
Engine/walk-forward/validation semua ada tapi TIDAK ada runner. Go-live gate (≥100 trades, DSR≥0.90, PBO≤0.30, OOS expectancy>0, PF≥1.15, max DD≤25%) tidak pernah dieksekusi vs data real. `harness.py:4` reference non-existent `scripts/run_backtest.py`.

### Context
Fungsi yang ada (verify signature):
- `backtest/walkforward.py`: `generate_windows(start_date, end_date, *, train_months, test_months, step_months)`, `run_walkforward_harness(strategy, strategy_cfg, df, *, cost_model, smart_exit=None, train_months, test_months, step_months, warmup_bars) -> WalkForwardHarnessResult`.
- `backtest/validation.py`: `run_validation_gates(metrics, n_trials=1, *, min_trades, min_expectancy, min_profit_factor, max_drawdown_pct, min_dsr_prob, max_pbo, pbo_value, permutation_p) -> ValidationGateResult`.
- `backtest/costs.py`: `load_cost_models(config_path) -> dict[str, CostModel]`.
- `persistence/repositories.py`: `CandleRepo`. Tambah `between(instrument_id, tf, start, end)` bila belum ada.
- `config/settings.yaml backtest.gates`: ekspresi string `"> 0"`, `">= 1.15"`, `">= 0.90"`, `0.30`, `25`.

### What changes
- File baru `src/rtrade/cli/backtest.py`.
- Tambah `CandleRepo.between(...)` bila belum ada.
- Tambah `parse_gate_expr(s)` helper untuk parse `"= 0.90"` → `0.90`.

### Code — `src/rtrade/cli/backtest.py`
[CODE] (~250 baris):
```python
"""Go-live statistical gate runner (FR-BT-01..05, G-04, ADR-A07).

Load candle range dari Postgres → walk-forward → validation gates →
persist backtest_runs → exit non-zero on failure.

Usage:
    python -m rtrade.cli.backtest --strategy s1_trend_pullback --symbol XAUUSD \
        --tf 1h --from 2025-01-01 --to 2026-01-01
"""

from __future__ import annotations
import argparse, re, sys
from datetime import date, datetime, UTC
from decimal import Decimal

import pandas as pd
import structlog

from rtrade.core.config import AppConfig
from rtrade.persistence.db import _get_engine, create_session_factory
from rtrade.persistence.repositories import CandleRepo, InstrumentRepo, BacktestRunRepo
from rtrade.strategies import STRATEGY_REGISTRY, StrategyConfig
from rtrade.backtest.costs import load_cost_models
from rtrade.backtest.walkforward import run_walkforward_harness
from rtrade.backtest.metrics import compute_metrics
from rtrade.backtest.validation import run_validation_gates

logger = structlog.get_logger(__name__)


def parse_gate_expr(s: str | float | int) -> float:
    """Parse '>= 0.90' / '= 1.15' / '> 0' / 0.30 → numeric threshold."""
    if isinstance(s, (int, float)): return float(s)
    m = re.search(r"-?\d+\.?\d*", str(s))
    if m is None: raise ValueError(f"cannot parse threshold from {s!r}")
    return float(m.group())


def main() -> None:
    ap = argparse.ArgumentParser(prog="rtrade-backtest")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--tf", default="1h")
    ap.add_argument("--from", dest="from_date", required=True, type=lambda d: date.fromisoformat(d))
    ap.add_argument("--to", dest="to_date", required=True, type=lambda d: date.fromisoformat(d))
    ap.add_argument("--baseline-strategy", default=None)  # FR-BT-07 comparison
    args = ap.parse_args()

    cfg = AppConfig.load()
    engine = _get_engine(cfg)
    sf = create_session_factory(engine)

    from rtrade.core.constants import Timeframe
    tf = Timeframe(args.tf)

    async def _run() -> int:
        async with sf() as session:
            inst = await InstrumentRepo(session).get_by_symbol(args.symbol)
            if inst is None:
                print(f"ERROR: instrument {args.symbol} not found", file=sys.stderr); return 2
            candles = await CandleRepo(session).between(inst.id, tf, args.from_date, args.to_date)
        if len(candles) < cfg.settings.backtest.min_trades_for_validation + 250:
            print(f"ERROR: insufficient candles ({len(candles)})", file=sys.stderr); return 2

        df = pd.DataFrame([{"ts": c.ts, "open": float(c.open), "high": float(c.high),
                            "low": float(c.low), "close": float(c.close),
                            "volume": float(c.volume)} for c in candles])
        df = df.set_index("ts").sort_index()

        strategy_cls = STRATEGY_REGISTRY.get(args.strategy)
        if strategy_cls is None:
            print(f"ERROR: unknown strategy {args.strategy}", file=sys.stderr); return 2
        strategy = strategy_cls()
        strategy_cfg = StrategyConfig.from_yaml(f"config/strategies/{args.strategy}.yaml")

        cost_models = load_cost_models()
        cost_model = cost_models.get(args.symbol)

        wf = run_walkforward_harness(strategy, strategy_cfg, df, cost_model=cost_model,
            train_months=cfg.settings.backtest.walkforward.train_months,
            test_months=cfg.settings.backtest.walkforward.test_months,
            step_months=cfg.settings.backtest.walkforward.step_months,
            warmup_bars=250)

        metrics = compute_metrics(wf.oos_r_multiples, wf.oos_equity_curve)
        gates = cfg.settings.backtest.gates
        vgr = run_validation_gates(
            metrics, n_trials=1,  # honest: single config per run
            min_trades=cfg.settings.backtest.min_trades_for_validation,
            min_expectancy=parse_gate_expr(gates.oos_expectancy_after_costs),
            min_profit_factor=parse_gate_expr(gates.oos_profit_factor),
            max_drawdown_pct=float(gates.max_drawdown_pct),
            min_dsr_prob=parse_gate_expr(gates.deflated_sharpe_prob),
            max_pbo=float(gates.pbo_max),
        )

        # Print human-readable report
        print(f"=== Backtest {args.strategy} / {args.symbol} {args.tf} ===")
        print(f"OOS trades: {vgr.n_trades_oos}")
        print(f"Expectancy: {vgr.expectancy_oos:.4f} R")
        print(f"Profit factor: {vgr.profit_factor_oos:.2f}")
        print(f"Max DD: {vgr.max_drawdown_pct:.2f}%")
        print(f"DSR prob: {vgr.dsr_probability:.4f}")
        print(f"PBO: {vgr.pbo:.4f}")
        for gate_id, info in vgr.gate_results.items():
            status = "PASS" if info["passed"] else "FAIL"
            print(f"  [{status}] {gate_id}: {info}")
        print(f"\nALL PASSED: {vgr.all_passed}")

        # Persist backtest_runs
        async with sf() as session:
            await BacktestRunRepo(session).add(
                strategy=args.strategy, instrument=args.symbol,
                window_start=args.from_date, window_end=args.to_date, is_oos=True,
                metrics={"n_trades_oos": vgr.n_trades_oos, "expectancy_oos": vgr.expectancy_oos,
                         "profit_factor_oos": vgr.profit_factor_oos,
                         "max_drawdown_pct": vgr.max_drawdown_pct,
                         "dsr_probability": vgr.dsr_probability, "pbo": vgr.pbo,
                         "n_trials": 1},
                gates={"all_passed": vgr.all_passed,
                       "per_gate": vgr.gate_results},
                params={"tf": args.tf,
                        "walkforward": cfg.settings.backtest.walkforward.model_dump(),
                        "baseline": args.baseline_strategy},
            )
            await session.commit()

        return 0 if vgr.all_passed else 1

    import asyncio
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
```

### Code — `CandleRepo.between` (bila belum ada, edit repositories.py)
[CODE]:
```python
async def between(self, instrument_id: int, tf: Timeframe,
                  start: date, end: date) -> list[CandleRow]:
    stmt = (select(CandleRow)
            .where(CandleRow.instrument_id == instrument_id,
                   CandleRow.timeframe == tf.value,
                   CandleRow.ts >= ensure_utc(datetime.combine(start, datetime.min.time())),
                   CandleRow.ts < ensure_utc(datetime.combine(end + timedelta(days=1), datetime.min.time())))
            .order_by(CandleRow.ts.asc()))
    result = await self._session.execute(stmt)
    return list(result.scalars().all())
```

### Code — `BacktestRunRepo.add` (bila belum ada)
[CODE] di repositories.py — persist ke model `BacktestRun` (models.py:147).

### Verification
```bash
mypy --strict src/rtrade/cli/backtest.py
python -m rtrade.cli.backtest --strategy s1_trend_pullback --symbol XAUUSD --tf 1h \
    --from 2025-01-01 --to 2026-01-01
psql -c "SELECT strategy, instrument, (gates->>'all_passed')::bool FROM backtest_runs ORDER BY id DESC LIMIT 5;"
```

### Acceptance [TEST] — `tests/backtest/test_cli_backtest.py` (QA-BT-01)
```python
async def test_cli_runs_and_persists(seeded_db_100plus_candles):
    """Seed DB dgn ≥100 candle + edge positif → CLI exit 0, row tertulis."""
    exit_code = await _invoke_cli(["--strategy","s1_trend_pullback","--symbol","XAUUSD",
                                    "--tf","1h","--from","2025-01-01","--to","2026-01-01"])
    assert exit_code == 0
    rows = await BacktestRunRepo(session).recent()
    assert len(rows) >= 1
    assert rows[0].gates["all_passed"] is True

async def test_cli_fails_on_noise_data(seeded_db_noise):
    """Noise dataset → fail ≥1 gate → exit non-zero."""
    exit_code = await _invoke_cli(...)
    assert exit_code == 1

async def test_thresholds_from_config():
    """Ubah threshold di settings.yaml → pass/fail flip tanpa kode."""
    ...
```

### Known pitfalls ⚠️
- Anti-lookahead: assert `run_walkforward_harness` hanya lihat `df.iloc[:i+1]` per window (FR-BT-03). Re-read harness.py; tambah assertion test.
- `n_trials=1` honest untuk single run — jangan game.
- Threshold parse: `">= 0.90"` → `0.90` via regex angka.

### Refs
G-04, FR-BT-01..05, DEF-REQ-05, QA-BT-01, ADR-A07, FR-STRAT-07 (gatekeeper promotion).

---

## P1-7 — Cold-start warmup guarantee (G-07) 🟠 (tarik maju P2, safety property)

### Why
Saat DB kosong/cold-start, scan bisa emit signal dari indicator/regime under-warmed (silent garbage). Wajib abstain.

### Context
`scan.py:219-226` punya `if len(df_1h) < 200: return insufficient_data`. Naikkan ke warmup window penuh.

### Code — edit `scan.py` run_scan warmup check
[CODE]:
```python
WARMUP_BARS = cfg.settings.signal.warmup_bars  # tambah field default 500 di SignalSettings

if len(df_1h) < WARMUP_BARS:
    await session.commit()
    await AuditRepo(session).add(stage="WARMUP", ok=False,
        detail={"bars_1h": len(df_1h), "required": WARMUP_BARS})
    return ScanResult(symbol=symbol, timeframe=tf.value, status="abstain_warmup",
                      detail={"bars_1h": len(df_1h), "required": WARMUP_BARS})
if Timeframe.H4 in instrument.timeframes and len(df_4h) < WARMUP_BARS:
    await session.commit()
    return ScanResult(symbol=symbol, timeframe=tf.value, status="abstain_warmup",
                      detail={"bars_4h": len(df_4h), "required": WARMUP_BARS})
```

### Code — edit `core/config.py` SignalSettings
[CODE] tambah:
```python
class SignalSettings(_StrictModel):
    # ... existing ...
    warmup_bars: int = Field(default=500, ge=200)
```

### Code — fix first-run underfetch (`scan.py:152-153`)
[CODE] di `_ingest_incremental`, cold-start `limit` naik dari 500 → 5000 (TwelveData max):
```python
if latest is None:
    since = now - timedelta(days=120)
    limit = 5000   # was 500 — full cold-start warmup (FR-DATA-09)
```
Dokumentasikan: 120-day H1 (~2880 bar) butuh backfill CLI untuk first run:
```bash
python -m rtrade.cli.backfill XAUUSD 1h --days 120
```

### Verification
```bash
pytest tests/integration/test_cold_start_warmup.py -x
```

### Acceptance [TEST] — `tests/integration/test_cold_start_warmup.py` (QA-INT-04)
```python
async def test_cold_start_abstains(empty_db):
    """DB kosong → first scan abstain_warmup, tidak publish."""
    result = await run_scan("XAUUSD", "1h", config=test_config, deliver=False)
    assert result.status == "abstain_warmup"

async def test_warmed_scan_resumes(seeded_db_500plus_bars):
    result = await run_scan("XAUUSD", "1h", config=test_config, deliver=False)
    assert result.status != "abstain_warmup"
```

### Refs
G-07, FR-DATA-09, FR-SIG-05, NFR-REL-08, QA-INT-04.

---

## P1-EXIT — Exit Gate Verification 🟠

**Jangan mulai P2 sebelum SEMUA ini hijau.**

### Commands
```bash
ruff check src/ tests/ && ruff format --check src/ tests/
mypy --strict src/
pytest tests/unit tests/property -x
pytest tests/integration -x
pytest --cov --cov-fail-under=80 src/rtrade/guardrails src/rtrade/backtest src/rtrade/data src/rtrade/risk src/rtrade/signals
pytest tests/unit -k selftest -x   # 13/13 gate
python -m rtrade.cli.backtest --strategy s1_trend_pullback --symbol XAUUSD --tf 1h --from 2025-01-01 --to 2026-01-01
```

### Exit Criteria (ALL must hold)
- ✅ ≥1 calendar source healthy ≥99.5% (staging); zero silent total-source outages (QA-INT-01).
- ✅ All 13 gates covered by selftest (QA-GATE-01); worker refuses start on injected bad gate.
- ✅ GR-09/10/11 exercised on post-LLM path (QA-GATE-02 via stub; llm.enabled stays false).
- ✅ GR-06 fails CLOSE bila required live quote missing (QA-GATE-03).
- ✅ `rtrade.cli.backtest` runs ≥100 trades end-to-end, records pass/fail, non-zero exit on fail (QA-BT-01).
- ✅ Cold-start scans abstain (QA-INT-04).
- ✅ `make ci` hijau. Coverage floors: guardrails ≥80, backtest ≥80, data/calendar ≥80, risk ≥80, signals ≥80, llm/verifier ≥80, others ≥60.

**STOP. Paste output. Tunggu konfirmasi sebelum P2.**

### Refs
DEF-REQ-03/04/05, FR-SCH-01, FR-GR-08, QA-GATE-01/02/03, QA-BT-01, QA-INT-01/04.

---

# PHASE P2 — ACCURACY, COST CONTROLS, OBSERVABILITY 🟡

> **Goal:** refine deterministic accuracy, harden LLM cost/abuse controls **sebelum** enablement, unify observability. Phase ini menetapkan bukti + cost ceiling agar `llm.enabled=true` di masa depan aman — tapi **tidak flip di production**.

## Item eksekusi P2 (urut)

| ID | Item | Prioritas |
|---|---|---|
| P2-1 | Feed `edge_quality_score` ke `grade_signal()` | 🟡 |
| P2-2 | True 24-bar `return_24h` (CRISIS) | 🟡 |
| P2-3 | `detect_gaps` rename + boundary tests | 🟡 |
| P2-4 | 4-cap LLM BudgetGuard (pre-enablement) | 🟠 HARD |
| P2-5 | Consolidate single alert path | 🟡 |
| P2-6 | Host-correct `check_disk` | 🟡 |
| P2-7 | Secrets/deploy hardening | 🟡 |
| P2-8 | Periodic audit-chain verify job | 🟡 |
| P2-9 | Staging-only LLM validation (akhir fase, opsional) | 🟠 |
| P2-EXIT | Verifikasi exit gate | 🟠 |

---

## P2-1 — Feed `edge_quality_score` ke `grade_signal()` (G-05) 🟡

### Why
Grade call site (`scan.py:1065-1073`) pass `edge_quality_score=None` padahal filter edge-quality sudah jalan & menghitung score. Dimensi edge inert di A/B/C grade.

### Context
`assess_edge_quality(...)` (`edge_quality.py:53`) return `EdgeQualityReport` dgn `.score` (int 0-100). `grade_signal(...)` (`grading.py:30`) terima param `edge_quality_score`. Grading A butuh `edge is None or edge≥80`; B butuh `edge≥65`.

### Code — edit `scan.py` grade call site
[CODE] (re-read `_run_strategies` sekitar `:849-1065` untuk variabel sebenarnya):
```python
# Setelah generate_candidate, capture edge score
edge_report = assess_edge_quality(df_1h, candidate.action, candidate.levels.entry_limit,
                                   spread=spread, config=edge_cfg)
edge_score: float | None = float(edge_report.score) if cfg.settings.signal.edge_quality.enabled else None

# ... di grade call site:
grade_res = grade_signal(
    confluence_score=candidate.confluence_score,
    regime_match=True,
    edge_quality_score=edge_score,   # was None — G-05 fix
    has_high_impact_event=high_impact_within(event_dicts, instrument.related_currencies, now, hours=12),
    confidence=float(confidence),
)
```
⚠️ Bila `generate_candidate` sudah run `assess_edge_quality` internal (reject filter), recompute di sini OK (input identik → score identik, pure function). Atau simpan score di candidate untuk hindari double compute.

### Acceptance [TEST] — `tests/unit/test_grading_edge.py` (DEF-REQ-07)
```python
def test_high_edge_vs_low_edge_different_grade():
    """confluence=80 + regime_match=True + edge=90 → grade A; edge=70 → grade B."""
    from rtrade.signals.grading import grade_signal
    a = grade_signal(confluence_score=80, regime_match=True, edge_quality_score=90,
                     has_high_impact_event=False, confidence=0.7)
    b = grade_signal(confluence_score=80, regime_match=True, edge_quality_score=70,
                     has_high_impact_event=False, confidence=0.7)
    assert str(a.grade) == "A"
    assert str(b.grade) == "B"

def test_edge_none_legacy_path():
    """edge_quality_score=None → grading tetap jalan (backwards-compat)."""
    ...
```

### Refs
G-05, FR-SIG-11, FR-SIG-13, FR-BL-05, DEF-REQ-07.

---

## P2-2 — True 24-bar `return_24h` (G-06) 🟡

### Why
`regime/rules.py:85` compute `return_24h = (close[-1] - close[-2]) / close[-2]` — return 1-bar di H1, bukan 24-bar. CRISIS sigma trigger approximate.

### Code — edit `src/rtrade/regime/rules.py` `classify`
[CODE] ganti block `return_24h`:
```python
# Compute TRUE 24-bar return (trailing 24×1h window) dan 90-bar stdev.
close = df["close"].astype(float)
return_24h: float | None = None
return_stdev: float | None = None
if len(close) >= 25:  # butuh 24 interval
    return_24h = float((close.iloc[-1] - close.iloc[-25]) / close.iloc[-25] * 100)
    if len(close) >= 90:
        # stdev dari per-bar returns 90-bar terakhir
        returns = close.pct_change().dropna().iloc[-90:]
        return_stdev = float(returns.std() * 100)
```
Dokumentasikan units: `return_24h` = % move over 24×1h bars; `return_stdev` = stdev per-bar return ×100; CRISIS if `|return_24h| ≥ 3 × return_stdev`.

### Acceptance [TEST] — `tests/property/test_return_24h_window.py` (QA-PROP-01)
```python
from hypothesis import given, strategies as st
import pandas as pd
from rtrade.regime.rules import RegimeClassifier

@given(st.lists(st.floats(min_value=1, max_value=1000, allow_nan=False), min_size=30, max_size=300))
def test_return_24h_uses_24_bar_window(closes):
    df = pd.DataFrame({"close": closes, "adx": [10]*len(closes), "atr_percentile": [50]*len(closes)})
    clf = RegimeClassifier()
    state = clf.classify("TEST", df)
    if state.return_24h is not None:
        expected = (closes[-1] - closes[-25]) / closes[-25] * 100
        assert abs(state.return_24h - expected) < 1e-6

def test_synthetic_24h_spike_triggers_crisis():
    """Move >3σ terkonsentrasi 24-bar → CRISIS; same move tersebar → tidak."""
    ...
```

### Refs
G-06, FR-REG-02, QA-PROP-01, DEF-REQ-07.

---

## P2-3 — `detect_gaps` rename + boundary tests (G-08) 🟡

### Why
Name collision: `data/ingestion.py:detect_gaps` (time-series gap) vs `indicators/structure.py:detect_gaps` (price FVG). Bug unit-of-72 (72 candles ≠ 72 jam di H4/D1).

### Code — edit `src/rtrade/data/ingestion.py`
[CODE] rename + fix heuristic:
```python
def detect_candle_gaps(candles, timeframe, *, is_crypto=False, max_consecutive_gaps=3):
    # ... existing body, tapi fix holiday heuristic:
    if not is_crypto:
        prev_weekday = candles[i - 1].ts.weekday()
        curr_weekday = actual_ts.weekday()
        if prev_weekday == 4 and curr_weekday == 0:  # Fri-close → Mon-open
            continue
        # Holiday gap: timeframe-aware (72 JAM, bukan 72 candle).
        td = timeframe_duration(timeframe)
        missing_hours = missing_count * (td.total_seconds() / 3600)
        if missing_hours <= 72 and prev_weekday >= 4:
            continue
```
Update call site (`ingestion.py:150`) + tests. Update FVG docstring di `indicators/structure.py` jelas bedakan.

### Acceptance [TEST] — `tests/unit/test_detect_candle_gaps.py` (QA-INT-03)
```python
def test_fx_friday_to_monday_not_flagged(): ...
def test_midweek_hole_flagged(): ...
def test_dst_boundary_handled(): ...
def test_h4_holiday_72_hours_not_candles(): ...
```

### Refs
G-08, FR-DATA-11, FR-DATA-12, QA-INT-03, DEF-REQ-07.

---

## P2-4 — 4-cap LLM BudgetGuard (G-11) 🟠 HARD precondition

### Why
Eksisting: `llm/key_manager.py` daily cost + 80% alert + Redis cooldown. **Missing:** per-scan wall-clock cap, per-scan step cap, hard per-day USD abort. Wajib sebelum `llm.enabled=true`.

### Code — `config/settings.yaml`
[CODE] tambah ke block `llm:`:
```yaml
llm:
  enabled: false
  ...
  budget:
    max_tokens_per_scan: 20000
    max_usd_per_day: 5.0
    max_wall_seconds_per_scan: 45
    max_steps_per_scan: 8
```

### Code — edit `core/config.py` LLMSettings
[CODE] tambah nested:
```python
class LLMBudgetSettings(_StrictModel):
    max_tokens_per_scan: int = Field(default=20000, ge=1)
    max_usd_per_day: float = Field(default=5.0, ge=0.01)
    max_wall_seconds_per_scan: float = Field(default=45.0, ge=1.0)
    max_steps_per_scan: int = Field(default=8, ge=1)

class LLMSettings(_StrictModel):
    # ... existing ...
    budget: LLMBudgetSettings = Field(default_factory=LLMBudgetSettings)
```

### Code — `src/rtrade/llm/budget_guard.py`
[CODE]:
```python
"""Layered LLM budget guard (FR-LLM-09/10/11, G-11).

4 caps independen: tokens/scan, USD/day (hard abort), wall-clock/scan,
steps/scan. Pricing dari litellm cost metadata (NFR-COST-02). Pada breach
apapun → set budget_stop + return reason. Cascade caller fallback/abstain.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
import time
from typing import Literal

import structlog

from rtrade.core.config import LLMBudgetSettings

logger = structlog.get_logger(__name__)

BudgetStopReason = Literal["tokens", "usd_day", "wall", "steps"]


@dataclass
class BudgetState:
    scan_tokens: int = 0
    scan_steps: int = 0
    scan_started_monotonic: float = field(default_factory=time.monotonic)
    day_usd: float = 0.0
    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    budget_stop: BudgetStopReason | None = None


class BudgetGuard:
    def __init__(self, caps: LLMBudgetSettings) -> None:
        self._caps = caps

    def start_scan(self) -> BudgetState:
        return BudgetState()

    def reset_day_if_needed(self, state: BudgetState) -> None:
        today = datetime.now(UTC).date()
        if state.day != today:
            state.day = today
            state.day_usd = 0.0
            state.budget_stop = None  # reset daily abort

    def record(self, state: BudgetState, *, tokens: int = 0, usd: float = 0.0,
               steps: int = 1) -> BudgetStopReason | None:
        if state.budget_stop is not None:
            return state.budget_stop  # sudah stop, short-circuit
        self.reset_day_if_needed(state)
        state.scan_tokens += tokens
        state.day_usd += usd
        state.scan_steps += steps
        elapsed = time.monotonic() - state.scan_started_monotonic

        if state.scan_tokens > self._caps.max_tokens_per_scan:
            state.budget_stop = "tokens"
        elif state.day_usd >= self._caps.max_usd_per_day:
            state.budget_stop = "usd_day"
        elif elapsed > self._caps.max_wall_seconds_per_scan:
            state.budget_stop = "wall"
        elif state.scan_steps > self._caps.max_steps_per_scan:
            state.budget_stop = "steps"

        if state.budget_stop:
            logger.warning("budget_stop triggered", reason=state.budget_stop,
                           scan_tokens=state.scan_tokens, day_usd=state.day_usd,
                           elapsed=elapsed, scan_steps=state.scan_steps)
        return state.budget_stop

    def at_80pct_daily(self, state: BudgetState) -> bool:
        return state.day_usd >= 0.8 * self._caps.max_usd_per_day
```

### Code — integrate di `llm/pipeline.py`
[CODE] (re-read pipeline.py untuk struktur `run_llm_pipeline` sebenarnya):
```python
# di awal run_llm_pipeline:
budget_state = budget_guard.start_scan()
alerted_80 = False

# setelah tiap model call (analyst/critic/verifier/escalation):
usd = _compute_cost(model_id, usage)  # litellm.completion_cost(...)
stop = budget_guard.record(budget_state, tokens=usage.total_tokens, usd=usd, steps=1)
if budget_guard.at_80pct_daily(budget_state) and not alerted_80:
    await _alert_80pct(budget_state.day_usd, caps.max_usd_per_day)
    alerted_80 = True
if stop:
    # clean abort: FR-LLM-03 fallback deterministic bila confluence ≥ 75, else ABSTAIN
    await _audit_budget_stop(candidate.candidate_id, stop, budget_state)
    return PipelineResult(decision="FALLBACK" if candidate.confluence_score >= 75 else "ABSTAIN",
                          confidence=..., budget_stop=stop, ...)
```

### Code — `_compute_cost` via litellm
[CODE]:
```python
import litellm
def _compute_cost(model: str, usage) -> float:
    try:
        return float(litellm.completion_cost(model=model, usage=usage))
    except Exception:
        return 0.0  # fallback bila model tak ada pricing
```

### Acceptance [TEST] — `tests/unit/test_budget_guard.py` (QA-LLM-01, DEF-REQ-06)
```python
from rtrade.core.config import LLMBudgetSettings
from rtrade.llm.budget_guard import BudgetGuard

def test_tokens_cap_triggers_stop():
    caps = LLMBudgetSettings(max_tokens_per_scan=100, max_usd_per_day=100,
                             max_wall_seconds_per_scan=100, max_steps_per_scan=100)
    g = BudgetGuard(caps); s = g.start_scan()
    assert g.record(s, tokens=50) is None
    assert g.record(s, tokens=60) == "tokens"

def test_usd_day_cap_hard_abort():
    caps = LLMBudgetSettings(max_tokens_per_scan=10**9, max_usd_per_day=1.0,
                             max_wall_seconds_per_scan=10**9, max_steps_per_scan=10**9)
    g = BudgetGuard(caps); s = g.start_scan()
    assert g.record(s, usd=0.5) is None
    assert g.record(s, usd=0.6) == "usd_day"
    assert g.record(s, usd=100) == "usd_day"  # short-circuit

def test_wall_clock_cap(): ...
def test_steps_cap(): ...
def test_utc_day_reset(): ...
def test_80pct_alert_threshold(): ...
```

### Known pitfalls ⚠️
- Pricing via `litellm.completion_cost` — bila model tak terdaftar, return 0 (jangan crash).
- Day reset UTC, bukan local.
- `budget_stop` short-circuit: setelah stop, semua call berikut return reason sama.
- `llm.enabled` STAYS false — guard di-test via unit, tidak di staging sampai akhir P2.

### Refs
G-11, FR-LLM-09/10/11, FR-GR-12 (GR-14), NFR-COST-01/02, DEF-REQ-06, QA-LLM-01, ADR-A06.

---

## P2-5 — Consolidate single alert path (G-12) 🟡

### Why
Dual path: inline `scheduler/jobs._send_failure_alert` (via TelegramDelivery) AND `monitoring/alerts.AlertManager` (typed, imported nowhere). Dedup/cooldown inkonsisten.

### Code — edit `scheduler/main.py run_worker()`
[CODE]:
```python
from rtrade.monitoring.alerts import AlertManager

async def run_worker() -> None:
    # ... existing logging/selftest ...
    cfg = AppConfig.load()
    alert_manager = AlertManager(
        bot_token=cfg.secrets.telegram_bot_token or "",
        chat_id=cfg.secrets.telegram_chat_id or "",
        enabled=bool(cfg.secrets.telegram_bot_token and cfg.secrets.telegram_chat_id),
    )
    import rtrade.scheduler.jobs as jobs_mod
    jobs_mod._alert_manager = alert_manager
    # ... scheduler.start() ...
```

### Code — edit `scheduler/jobs.py`
[CODE] replace `_send_failure_alert` usage dgn alert_manager:
```python
_alert_manager: AlertManager | None = None  # set by run_worker

async def _emit_alert(alert_type, level, title, message):
    if _alert_manager is not None:
        await _alert_manager.send_alert(alert_type, level, title, message)
```
Update semua caller: scan_fail → `AlertType.SCAN_FAILED`, rate-limit → `AlertType.KEY_EXHAUSTED` (atau add `RATE_LIMIT` ke enum), calendar → `AlertType.PROVIDER_DOWN`, budget → `AlertType.BUDGET_ALERT`.

### Code — re-point composite `_calendar_alert` (scan.py)
[CODE]:
```python
async def _calendar_alert(message: str) -> None:
    from rtrade.scheduler.jobs import _emit_alert
    from rtrade.monitoring.alerts import AlertType, AlertLevel
    await _emit_alert(AlertType.PROVIDER_DOWN, AlertLevel.WARNING, "Calendar", message)
```

### Acceptance [TEST] — `tests/integration/test_alert_dedup.py` (DEF-REQ-08)
```python
async def test_single_condition_single_alert(mock_telegram):
    """Satu triggering condition → exactly one alert (dedup)."""
async def test_cooldown_respected():
    """Same alert dalam cooldown → tidak re-fire."""
```

### Refs
G-12, FR-OBS-01, NFR-MAINT-03, DEF-REQ-08, ADR-A09.

---

## P2-6 — Host-correct `check_disk` (G-14) 🟡

### Code — edit `src/rtrade/monitoring/healthcheck.py`
[CODE]:
```python
import os, platform, shutil

def _disk_path() -> str:
    if platform.system() == "Windows":
        return os.environ.get("SYSTEMDRIVE", "C:") + "\\"
    return "/"

def check_disk(path: str | None = None) -> dict:
    target = path or _disk_path()
    usage = shutil.disk_usage(target)
    pct = usage.used / usage.total * 100
    return {"path": target, "used_pct": pct, "healthy": pct < 85}
```
Tambah `monitoring.disk_path: str | None` ke config (None → per-OS).

### Refs
G-14, FR-OBS-02, NFR-MAINT-04.

---

## P2-7 — Secrets/deploy hardening (G-20) 🟡

### Code — `docs/RUNBOOK.md`
[CODE] (outline):
```markdown
# Robil Trade VPS Runbook

## Secrets
- `.env` di VPS: `chmod 600 .env`, owner `rtrade:rtrade`.
- Rotasi bearer token (`API_AUTH_TOKEN`): edit `.env`, `docker compose restart api`.
- Jangan commit `.env`. Verify: `git status` clean.

## Calendar keys
- Finnhub (paid): https://finnhub.io/dashboard → rotate, update `FINNHUB_API_KEY`.
- Nasdaq Data Link: https://data.nasdaq.com/account/profile → `NDAQ_API_KEY`.

## Caddy TLS/headers
- Auto-TLS via Caddy (443). HSTS, X-Content-Type-Options, X-Frame-Options.
- Verify: `curl -sI https://robil.example` → header ada.

## Backup
- `scripts/backup_db.sh` daily 03:00 (cron container). Verify: `ls -la /backups/`.
```
Verifikasi structlog redaction cover pattern baru. Tambah log-scan test dgn fake secrets.

### Refs
G-20, NFR-SEC-02/03/05/08, ADR-A11.

---

## P2-8 — Periodic audit-chain verify job (FR-OBS-05) 🟡

### Code — edit `scheduler/main.py` add job
[CODE]:
```python
async def audit_chain_verify_job() -> None:
    from rtrade.persistence.audit_chain import verify_chain
    cfg = AppConfig.load()
    engine = _get_engine(cfg); sf = create_session_factory(engine)
    async with sf() as session:
        broken = await verify_chain(session)
    if broken:
        from rtrade.scheduler.jobs import _emit_alert
        from rtrade.monitoring.alerts import AlertType, AlertLevel
        await _emit_alert(AlertType.SERVICE_UNHEALTHY, AlertLevel.CRITICAL,
                          "Audit chain", f"{len(broken)} broken prev_hash links: {broken[:3]}")

# di create_scheduler, wrap via _run_job:
scheduler.add_job(lambda: _run_job("audit_chain_verify", audit_chain_verify_job),
                  CronTrigger(hour="3", minute="30", timezone="UTC"),
                  id="audit_chain_verify", replace_existing=True)
```

### Acceptance [TEST]
```python
async def test_corrupt_row_triggers_alert(seeded_db):
    # corrupt 1 prev_hash → job detect + alert
```

### Refs
FR-OBS-05, NFR-REL-07, FR-SIG-17.

---

## P2-9 — Staging-only LLM validation (akhir fase, opsional) 🟠

### Steps (staging)
1. Flip `llm.enabled: true` di staging `.env` saja.
2. Run scan, observe: cascade jalan, budget_guard abort pada cap, GR-09/10/11 fire post-LLM gate, disclaimer/sizing preserved.
3. Production `llm.enabled` STAYS false.

### Refs
FR-LLM staging, ADR-A06, NFR-COST-04, FR-PT-07.

---

## P2-EXIT — Exit Gate Verification 🟠

### Exit Criteria (ALL must hold)
- ✅ Grade composition include edge dimension (P2-1 regression green).
- ✅ Regime/ingestion P2 tests green (P2-2, P2-3).
- ✅ LLM budget caps abort cleanly, 0 overruns staging (P2-4); 80% alert fires.
- ✅ Single alert path (P2-5); host-correct disk (P2-6).
- ✅ Paper-track rolling 30-trade OOS expectancy >0 R, PF≥1.15 sustained.
- ✅ Audit-chain verify_chain passes 100% rows periodic job (P2-8); calendar-source + LLM-budget telemetry di `/health`/`/metrics`.
- ✅ `make ci` hijau.

**STOP. Paste output. Tunggu konfirmasi sebelum P3.**

### Refs
DEF-REQ-06/07/08, FR-SIG-11/13, FR-REG-02, FR-LLM-09/10/11, FR-OBS-01/02/04/05, SM-04/06/07.

---

# PHASE P3 — ENHANCEMENTS (accuracy-positive only) 🟢

> **Goal:** layer enhancement akurasi/operabilitas SETELAH data/safety/validation solid. Tiap enhancement HARUS individually demonstrate improvement — atau stay inert/shadow. **Tidak boleh melemahkan guardrail atau fail-CLOSE default.**

## P3-1 — Real Telegram commands (G-13)
Back `/status`, `/signals`, `/calibration`, `/enable_strategy` dgn repository/route sama dgn FastAPI. `/enable_strategy <name>` flip `strategy_state.enabled=true` via `StrategyStateRepo` + audit, whitelist `TELEGRAM_CHAT_ID`. `/mute Nh` suppress push tanpa drop paper-track.
**Acceptance:** commands return live DB data; `/enable_strategy` flips state; unauthorized ditolak.
**Refs:** G-13, FR-DEL-02..06, DEF-REQ-09, QA-OBS-04.

## P3-2 — Unified `rtrade` entrypoint (G-15)
`[project.scripts] rtrade = "rtrade.cli.__main__:main"` dispatch `auth|backfill|bot|backtest`.
**Refs:** G-15, FR-OBS-07, NFR-MAINT-06.

## P3-3 — Dedupe `FUNDING_EXTREME_ABS` (G-19)
Hapus inline di `scan.py:91-92`, import dari `data/derivatives.py:6`. `grep` assert 1 definition.
**Refs:** G-19, FR-SIG-06, NFR-MAINT-05.

## P3-4 — Crypto Fear & Greed (G-18)
`src/rtrade/data/fear_greed.py` keyless `alternative.me` daily (BTC/ETH only). Soft de-risk macro slot (only reduce). FX/metals unaffected. Default disabled.
**Refs:** G-18, FR-DATA-22, FR-BL-08, FR-SIG-09.

## P3-5 — HMM shadow + River/ADWIN drift (G-17) — SHADOW ONLY
Shadow-agreement metric + `river` ADWIN drift → REGIME_SHADOW audit. No auto promotion. Gate behind P1 backtest (ADR-A08). No Qlib/RDAgent/FinRL.
**Refs:** G-17, FR-REG-06, FR-BL-07, ADR-A08, DEF-REQ-10.

## P3-6 — `ml.meta_label` ONLY if beats OOS expectancy (G-16)
Align triple-barrier labels dgn papertrack SL-first. **No `predict()` di scan.py** kecuali backtest_runs show OOS-expectancy improvement. Default INERT.
**Refs:** G-16, FR-BL-13, DEF-REQ-10, ADR-A08.

## P3-7 — (Optional) LLM polish
FR-BL-11 Reflexion critic verdict; FR-BL-12 in-process tool interface (no network MCP); FR-BL-09 news-velocity. Hanya setelah production LLM enabled & accuracy-positive.

## P3-EXIT — Per-enhancement Exit Gate 🟢
- ✅ Tiap enhancement demonstrate improvement — atau inert/shadow.
- ✅ Tidak melemahkan guardrail/fail-CLOSE; HMM/meta-label shadow/inert unless beat OOS via P1 backtest gate.
- ✅ `make ci` hijau.

**Refs:** FR-DEL-02..06, FR-OBS-07, FR-DATA-22, FR-REG-06, ADR-A08, DEF-REQ-09/10, QA-OBS-04.

---

# Bagian Akhir — Test Pyramid, CI, Coverage, Checklist, Traceability

## 9.1 Test Pyramid (bangun inkremental per item)

| Tier | Scope | Tooling | Lokasi |
|---|---|---|---|
| Unit | Fungsi pure: indicators, structure, levels, confluence weighting, risk sizing, Kelly, semua 13 gate isolasi, DSR/PBO metrics, verifier parsing, regime rules, calendar parsers | pytest, hypothesis | `tests/unit/` |
| Property | Invariant untuk semua input: RR≥1.5 (GR-03), SL∈[0.5,3.0]×ATR (GR-04), risk≤2% (GR-05), `return_24h` window==24 (G-06), frozen-candidate immutability (GR-02) | pytest + hypothesis | `tests/property/` |
| Integration | DB repositories, providers (HTTP mock respx + recorded fixtures), full scan pipeline end-to-end dgn LLM **mocked**, composite calendar fallback transitions | pytest-asyncio, respx, freezegun | `tests/integration/` |
| Backtest-validation | Walk-forward + validation-gate stack vs real candle range; go-live statistical gate benar-benar dieksekusi (G-04) | `rtrade.cli.backtest` driving `backtest/walkforward.py`, `validation.py` | `tests/backtest/` + CI nightly |
| Guardrail selftest | Semua 13 gate (GR-01..GR-13) exercise dgn known-bad candidate saat worker startup; SystemExit pada failure (G-10) | `guardrails/selftest.py` | in-process boot **dan** CI job |
| Live smoke | Satu real call per data provider & per LLM model; manual pre-deploy only | `make smoke` | manual, gated |

**Determinism rule:** setiap tes yang menyentuh time WAJIB `freezegun`; setiap tes yang menyentuh HTTP WAJIB `respx` dgn recorded fixtures. Tidak ada live network kecuali tier `make smoke`.

## 9.2 Functional QA requirements (mapping)

| ID | Requirement | Verifies |
|---|---|---|
| QA-UNIT-01 | Unit tests cover semua pure function di `risk/`, `guardrails/`, `signals/`, `regime/rules.py`, dan setiap concrete `CalendarProvider` parser | G-01, correctness |
| QA-PROP-01 | Property tests pin `return_24h` ke true trailing 24-bar window; fail bila dari `close[-1] vs close[-2]` | G-06 |
| QA-PROP-02 | Property tests assert GR-03/04/05 floors hold untuk randomized candidate; violating candidate always REJECTED | risk-safety invariant |
| QA-INT-01 | CompositeCalendarProvider integration: primary fail → secondary success → static fallback, per-source `last_success` update + fallback-transition alert tiap hop | G-01, G-02 |
| QA-INT-02 | E2E scan integration (LLM mocked) produce PUBLISHED non-crypto signal saat calendar window fresh — GR-07b no longer dominant rejector | success metric |
| QA-INT-03 | `detect_gaps` tests cover Fri-close/Sun-open FX boundaries + DST; name-collision disambiguated | G-08 |
| QA-INT-04 | Cold-start integration: scans abstain saat < warmup window (≥500 bars 1h+4h) | G-07 |
| QA-GATE-01 | `guardrails/selftest.py` known-bad fixture per GR-01..GR-13; CI fail + worker SystemExit bila gate pass bad input | G-10 |
| QA-GATE-02 | Test assert `run_gate()` di `scan.py` receive `confidence`, `original_candidate`, `sources`, `pack_source_ids` post-LLM | G-03 |
| QA-GATE-03 | Test assert GR-06 fail-CLOSE (abstain) saat required live quote unavailable; FX no-spread graceful-degrade path asser terpisah | G-09 |
| QA-LLM-01 | Budget-guard tests: tiap 4 cap abort cleanly dgn `budget_stop` + emit 80% alert; no cap bypassable | G-11 |
| QA-BT-01 | `rtrade.cli.backtest` integration: load ≥100 trades seeded test DB, run gates, persist `backtest_runs`, exit non-zero on fail | G-04 |

## 9.3 Coverage floors (per-package, NFR-QA-01)

Coverage di-enforce per-package, bukan global, agar package safety-critical tidak diencerkan oleh glue code mudah.

| Package | Floor | Rationale |
|---|---|---|
| `risk/` | ≥80% | sizing/limits = risk-safety invariant |
| `guardrails/` | ≥80% | 13-gate fail-CLOSED contract |
| `signals/` | ≥80% | grade composition (incl `edge_quality_score`, G-05) |
| `backtest/` | ≥80% | go-live statistical gate |
| `llm/verifier.py` | ≥80% | hallucination blast-radius control |
| `data/` calendar providers (new) | ≥80% | v2 hot path; fail-CLOSED dependency |
| all other `src/` modules | ≥60% | baseline |

## 9.4 CI pipeline (NFR-CI-01, ordered fast→slow)

```
1. ruff check            # lint
2. ruff format --check   # format drift
3. mypy --strict src/    # types (whole src/, no exemptions)
4. pytest tests/unit tests/property      # fast, hermetic
5. pytest tests/integration              # respx/freezegun, DB via service container
6. pytest --cov, enforce per-package floors (NFR-QA-01)
7. guardrail selftest job (QA-GATE-01)   # all 13 gates
8. uv lock --check + pip-audit/uv audit  # dependency integrity (committed lockfile)
9. gitleaks scan                         # no secrets in history
```

Backtest-validation suite (`tests/backtest/`, `rtrade.cli.backtest` on seeded data) jalan sebagai **nightly** CI job, bukan per-commit (lambat + data-heavy). Pass/fail direkam tapi tidak block commit biasa. **Block release tag.**

| ID | Requirement |
|---|---|
| NFR-CI-01 | Semua 9 per-commit step hijau sebelum merge ke release branch. |
| NFR-CI-02 | Committed `uv.lock` match `pyproject.toml`; `pip-audit`/`uv audit` jalan di CI dgn cadence monthly. |
| NFR-CI-03 | Repo local-only: `make ci` = ekuivalen kontraktual GitHub Actions; WAJIB sebelum setiap release tag. |
| NFR-CI-04 | Licensing guard step grep diff untuk string attributable FinceptTerminal provenance; match fail build (defense ADR-A10). |

## 9.5 Deployment ke DigitalOcean VPS (Docker + Caddy)

Target: Ubuntu 24.04, `ssh robil-vps` (143.198.195.94, sgp1), min **2 vCPU / 4 GB RAM / 50 GB disk**. Single multi-stage `Dockerfile` shared worker/api/bot, via `docker-compose.yml` + `docker-compose.prod.yml`. Caddy only publisher port 443; `db`/`redis` internal network.

```bash
# One-time server prep
ssh robil-vps
apt update && apt upgrade -y
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 443 && ufw enable
adduser rtrade --disabled-password && usermod -aG docker rtrade

# Per-release deploy
rsync -az --exclude .env --exclude .git ./robil-trade/ rtrade@robil-vps:/opt/robil-trade/
scp .env.prod rtrade@robil-vps:/opt/robil-trade/.env       # lalu: chmod 600 .env
ssh robil-vps 'cd /opt/robil-trade && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build'

# Verify
ssh robil-vps 'cd /opt/robil-trade && docker compose ps && curl -s localhost:8000/health'
# + confirm bot startup message di Telegram channel
```

| ID | Requirement |
|---|---|
| NFR-DEPLOY-01 | Semua container `restart: unless-stopped`; restart-on-reboot verify dgn reboot test. |
| NFR-DEPLOY-02 | `.env` di host permission 600, di luar git, exclude dari deploy rsync. |
| NFR-DEPLOY-03 | Hanya Caddy publish port (443, TLS auto); `db`/`redis` tidak pernah exposed external; API/dashboard hanya via Caddy dgn bearer auth + per-IP throttle. |
| NFR-DEPLOY-04 | `/health` (disk check host-correct G-14) return healthy DAN guardrail selftest passed (no SystemExit) sebagai release acceptance gate. |

## 9.6 Risk/Edge-Case Checklist (read sebelum tiap item)

- **Async purity:** CPU-bound indicator/structure/regime work offload via `asyncio.to_thread` (P0 fix #1 in tree — preserve). Provider kalender baru I/O only (fine async).
- **Timezone discipline:** setiap `datetime` tz-aware UTC. `ensure_utc(...)` di setiap domain boundary. Naive datetime raise `DataValidationError`. Pin clock di test dgn `freezegun`.
- **Decimal vs float:** `Candle` OHLC `Decimal` (domain/DB); indicators/levels/R-multiple `float` setelah `_candles_to_df`. Jangan campur.
- **Idempotency:** re-ingest/re-scan idempotent (dedup keys: `(instrument, timeframe, ts)` candles; `(instrument, timeframe, strategy, bar_ts)` signals; `event_id` events).
- **No look-ahead:** drop forming bars (`last_closed_candle_open`); backtest signals hanya lihat `df.iloc[:i+1]`; decide-on-close, fill-next-open; SL-first pada bar ambigu.
- **Don't break frozen candidate:** `SignalCandidate` frozen; never mutate levels/size setelah konstruksi.
- **Provider errors typed:** transient (`httpx.TransportError`) → bounded retry; 429 → `RateLimitExceeded` (bucket handle); 4xx/5xx → `ProviderError` (no retry). Never double-wait rate limits (P0 fix #4 in tree — preserve).
- **Single alert path:** setelah P2-5, semua alert via `AlertManager`. Jangan re-introduce inline alert sending.
- **`llm.enabled` stays false** di CI & production sepanjang P2; hanya staging akhir P2.
- **Verify, don't assume:** setelah tiap item, run subset tes relevan + paste output. Setelah tiap fase, run `make ci` + paste output. Never claim done tanpa green output.
- **When unsure signature/call site:** READ file dulu (PRD line number drift saat edit). Facts di dokumen ini akurat per review tapi re-confirm sebelum edit.

## 9.7 Execution Order Summary (strict, jangan lompat)

**P0:** P0-1 (ADR) → P0-2 (config) → P0-3 (primary provider) → P0-4 (static provider) → P0-5 (composite) → P0-8 (health table) → P0-6 (wire + verify GR-07b unblocks) → P0-7 (startup warning). **→ P0 EXIT verification.**

**P1:** P1-1 (secondary provider) → P1-2 (health telemetry) → P1-3 (GR-09/10/11 wiring) → P1-4 (13-gate selftest) → P1-5 (GR-06 fail-close) → P1-6 (backtest CLI) → P1-7 (cold-start warmup). **→ P1 EXIT verification.**

**P2:** P2-1 (edge→grade) → P2-2 (return_24h) → P2-3 (detect_gaps) → P2-4 (4-cap budget guard) → P2-5 (single alert path) → P2-6 (check_disk) → P2-7 (secrets hardening) → P2-8 (audit-chain job) → P2-9 (staging LLM, optional). **→ P2 EXIT verification.**

**P3:** P3-1 (Telegram commands) → P3-2 (unified CLI) → P3-3 (dedupe constant) → P3-4 (F&G) → P3-5 (HMM shadow gate, shadow only) → P3-6 (meta_label, inert unless gate-proven) → P3-7 (optional LLM polish). **→ P3 EXIT verification.**

## 9.8 Traceability Matrix (FR ↔ Task ↔ Test ↔ GAP)

| GAP | Task | Primary Test | FR/NFR |
|---|---|---|---|
| G-01 (Finnhub 403 → GR-07b) | P0-3/4/5/6 | QA-INT-02 | FR-CAL-01/08, DEF-REQ-01, SM-01 |
| G-02 (single source fragile) | P0-5, P1-1 | QA-INT-01 | FR-CAL-02/05, NFR-REL-01 |
| G-03 (GR-09/10/11 dormant) | P1-3 | QA-GATE-02 | FR-GR-04/05/06/07, DEF-REQ-03 |
| G-04 (no backtest runner) | P1-6 | QA-BT-01 | FR-BT-01..05, DEF-REQ-05 |
| G-05 (edge inert in grade) | P2-1 | test_grading_edge | FR-SIG-11/13, DEF-REQ-07 |
| G-06 (return_24h 1-bar) | P2-2 | QA-PROP-01 | FR-REG-02 |
| G-07 (cold-start underfetch) | P1-7 | QA-INT-04 | FR-DATA-09, FR-SIG-05, NFR-REL-08 |
| G-08 (detect_gaps collision) | P2-3 | QA-INT-03 | FR-DATA-11/12 |
| G-09 (GR-06 fail-open) | P1-5 | QA-GATE-03 | FR-GR-03, FR-DATA-13, NFR-REL-05 |
| G-10 (selftest 3/13) | P1-4 | QA-GATE-01 | FR-GR-08, FR-SELFTEST-01, FR-SCH-01, DEF-REQ-04 |
| G-11 (LLM cost shallow) | P2-4 | QA-LLM-01 | FR-LLM-09/10/11, NFR-COST-01, DEF-REQ-06 |
| G-12 (dual alert path) | P2-5 | test_alert_dedup | FR-OBS-01, NFR-MAINT-03, DEF-REQ-08 |
| G-13 (Telegram stub) | P3-1 | QA-OBS-04 | FR-DEL-02..06, DEF-REQ-09 |
| G-14 (check_disk Windows) | P2-6 | test_healthcheck_disk | FR-OBS-02, NFR-MAINT-04 |
| G-15 (no console script) | P3-2 | smoke rtrade | FR-OBS-07, NFR-MAINT-06 |
| G-16 (meta_label inert) | P3-6 | (inert unless gate) | FR-BL-13, DEF-REQ-10 |
| G-17 (HMM no drift gate) | P3-5 | (shadow only) | FR-REG-06, FR-BL-07, DEF-REQ-10 |
| G-18 (no crypto F&G) | P3-4 | (soft de-risk) | FR-DATA-22, FR-SIG-09 |
| G-19 (FUNDING_EXTREME dup) | P3-3 | grep 1 def | FR-SIG-06, NFR-MAINT-05 |
| G-20 (secrets/deploy) | P2-7 | log-scan + runbook | NFR-SEC-02/03/05/08 |
| G-21 (Fincept AGPL) | P0-1 | ADR existence | NFR-LEG-02, DEF-REQ-02, ADR-A10 |

## 9.9 Final Reminders to the Implementing Agent

1. **Pertama dan paling penting:** restore non-crypto coverage (P0) tanpa melemahkan guardrail mana pun. Lakukan P0 dulu dan verifikasi sebelum apa pun lagi.
2. **Run `make ci` setelah setiap fase** dan paste output sebelum klaim done.
3. **PRD menang** bila ada kontradiksi — re-read FR/NFR/ADR relevan.
4. **Never copy FinceptTerminal code** (ADR-A10). Semua ide re-implement independen atau via library permissive.
5. **Never enable LLM in production** sebelum P2 exit + paper-track expectancy terpenuhi.
6. **Never weaken GR-03/04/05 atau calendar fail-CLOSE default.**
7. **Verify, don't assume** — paste output asli untuk setiap klaim.
8. **Setiap pesan signal WAJIB disclaimer Bahasa-Indonesia + manual-execution framing.** Tidak ada auto-trade, tidak ada profit guarantee.

---

**End of Implementation Plan v2.** Total ~2700 baris. Dokumen ini adalah instruksi kerja lengkap; ikuti execution order (§9.7) ketat, verifikasi setiap exit gate, dan jangan ragu untuk re-read PRD section terkait bila ragu.
