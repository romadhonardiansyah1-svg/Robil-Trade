# IMPLEMENTATION TASKS 4 — MILESTONE 5: AKTIVASI & PEMBUKTIAN (V1–V6)

> Status sistem: SEMUA fitur ter-wire (F1–F7 ✅, W1–W10 ✅ + perbaikan b023c50). ruff/mypy/pytest
> bersih. TAPI: bot ini **belum pernah dijalankan end-to-end dengan data nyata** — belum ada
> backfill, belum ada backtest sungguhan (scripts/run_backtest.py masih stub TODO!), LLM belum
> pernah menyala, belum ada paper trading. Menambah fitur lagi sekarang = membangun lantai 10
> di atas fondasi yang belum diuji beban.
>
> Milestone 5 = NYALAKAN & BUKTIKAN. Milestone 6 (scalping M15, S3 sweep, volume profile,
> champion/challenger) DIKUNCI sampai gate validasi Milestone 5 lolos.
>
> Aturan kerja: sama dengan IMPLEMENTATION_TASKS.md §0 + BUKTI Select-String.
> Task bertanda **[USER]** butuh tindakan user (API key/VPS) — agen menyiapkan, user mengeksekusi.

---

## V1 — Backtest harness NYATA (ganti stub scripts/run_backtest.py)

**Ini prasyarat semua validasi.** Stub lama berisi TODO — ganti dengan implementasi penuh.

**File**: `scripts/run_backtest.py` (tulis ulang total; boleh tambah modul
`src/rtrade/backtest/harness.py` agar logika bisa diunit-test).

### Spesifikasi `src/rtrade/backtest/harness.py` (modul baru, pure + DB loader terpisah)
```python
"""Backtest harness: strategy → bar-by-bar signals → engine → metrics → gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from rtrade.backtest.costs import CostModel
from rtrade.backtest.engine import BacktestResult, run_backtest
from rtrade.backtest.metrics import BacktestMetrics, compute_metrics
from rtrade.backtest.permutation import permutation_pvalue
from rtrade.backtest.smart_exit import SmartExitConfig
from rtrade.backtest.validation import ValidationGateResult, run_validation_gates
from rtrade.core.constants import Regime
from rtrade.regime.rules import RegimeClassifier
from rtrade.strategies.base import Strategy, StrategyConfig


@dataclass(frozen=True)
class HarnessResult:
    signals: list[dict[str, object]]
    backtest: BacktestResult
    metrics: BacktestMetrics
    gates: ValidationGateResult
    permutation_p: float


def generate_signals(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    df: pd.DataFrame,
    *,
    warmup_bars: int = 250,
    window_bars: int = 400,
    valid_bars: int = 6,
) -> list[dict[str, object]]:
    """Walk bar-by-bar; at each closed bar evaluate the strategy on a tail window.

    ANTI-LOOKAHEAD: indikator dihitung SEKALI di df penuh — aman karena semua
    indikator kausal (EMA/RSI/ATR/ADX/rolling hanya melihat masa lalu).
    entry_signal()/custom_entry_price() hanya menerima irisan df.iloc[:i+1]
    (di-tail window_bars untuk kecepatan), jadi bar i tidak pernah melihat i+1.
    Regime dihitung on-the-fly dengan classifier stateful (hysteresis benar).
    """
    df = strategy.populate_indicators(df.copy(), strategy_cfg)
    classifier = RegimeClassifier()
    signals: list[dict[str, object]] = []

    for i in range(warmup_bars, len(df)):
        window = df.iloc[max(0, i + 1 - window_bars) : i + 1]
        regime = classifier.classify("BT", window)
        if regime.regime != strategy.required_regime:
            continue
        intent = strategy.entry_signal(window)
        if intent is None:
            continue
        try:
            levels = strategy.custom_entry_price(window, intent)
        except (ValueError, IndexError):
            continue
        if not strategy.confirm_signal(window, levels):
            continue
        # GR-03/04 ringkas (mirror validate_and_round_levels tanpa pip rounding):
        sl_dist = abs(levels.entry_limit - levels.stop_loss)
        tp_dist = abs(levels.take_profit - levels.entry_limit)
        if sl_dist <= 0 or tp_dist / sl_dist < 1.5:
            continue
        atr_mult = sl_dist / levels.atr_at_signal
        if not (0.5 <= atr_mult <= 3.0):
            continue
        signals.append(
            {
                "bar_index": i,
                "direction": intent.action.value,
                "entry_limit": levels.entry_limit,
                "stop_loss": levels.stop_loss,
                "take_profit": levels.take_profit,
                "valid_bars": valid_bars,
            }
        )
    return signals


def run_harness(
    strategy: Strategy,
    strategy_cfg: StrategyConfig,
    df: pd.DataFrame,
    *,
    cost_model: CostModel | None,
    smart_exit: SmartExitConfig | None = None,
    n_trials: int = 1,
) -> HarnessResult:
    signals = generate_signals(strategy, strategy_cfg, df)
    bt = run_backtest(df, signals, cost_model=cost_model, smart_exit=smart_exit)
    r = [t.r_multiple for t in bt.trades if t.r_multiple is not None]
    metrics = compute_metrics(r, bt.equity_curve)
    perm_p = permutation_pvalue(r, len(df)) if r else 1.0
    gates = run_validation_gates(metrics, n_trials, permutation_p=perm_p)
    return HarnessResult(
        signals=signals, backtest=bt, metrics=metrics, gates=gates, permutation_p=perm_p
    )
```
CATATAN: cek signature `run_validation_gates` — parameter `permutation_p` sudah ditambahkan W7.
Cek juga `permutation_pvalue(r_multiples, n_bars, ...)` — sesuaikan argumen dengan implementasi
yang ada (baca file dulu).

### Spesifikasi `scripts/run_backtest.py` (tulis ulang)
1. Args: `--strategy s1_trend_pullback|s2_range_mr`, `--instrument`, `--smart-exit` (flag),
   `--report` (default true).
2. Load AppConfig; DB engine; ambil SEMUA candle 1H instrumen dari DB
   (`CandleRepo.get_range(start=2000-01-01, end=now)` → df via pola `_candles_to_df`).
   Kalau < 5000 bar → exit dengan pesan "jalankan backfill dulu" (lihat V4).
3. `compute_indicators(df)` → harness → tulis laporan markdown ke
   `reports/backtest_{strategy}_{instrument}_{date}.md` berisi:
   n_signals, n_trades, win_rate, expectancy, profit_factor, max_dd, sharpe, DSR prob,
   permutation_p, tabel gates PASS/FAIL, dan 10 trade pertama (debug).
4. Simpan `BacktestRun` row ke DB (model sudah ada — isi strategy, instrument, params,
   window, metrics dict, gates dict).
5. Mode perbandingan exit: `--smart-exit` menjalankan DUA backtest (tanpa & dengan
   SmartExitConfig default partial 0.5@1R + BE) di sinyal yang SAMA, laporkan keduanya
   berdampingan + delta expectancy.

**Test baru** `tests/unit/test_harness.py`:
- df sintetis 600 bar tren naik kuat (pola dari test_signals) → `generate_signals(S1...)`
  menghasilkan ≥ 1 sinyal; semua sinyal punya `bar_index >= 250`; RR tiap sinyal ≥ 1.5.
- Anti-lookahead smoke: `generate_signals` pada df[:500] dan df penuh → sinyal dengan
  bar_index < 500 IDENTIK (bar masa depan tidak mengubah masa lalu).
  (Catatan: RegimeClassifier stateful baru per panggilan → deterministik.)
- `run_harness` end-to-end di df sintetis: HarnessResult lengkap, gates dict terisi.

**BUKTI**:
```powershell
Select-String -Path scripts/run_backtest.py -Pattern "run_harness|generate_signals" | Measure-Object  # >= 2
Select-String -Path scripts/run_backtest.py -Pattern "TODO" | Measure-Object                          # == 0
```
**Commit**: `feat(backtest): real strategy→engine harness replaces stub runner (V1)`

---

## V2 — Walk-forward nyata + laporan validasi resmi

1. `src/rtrade/backtest/harness.py` — fungsi tambahan:
   ```python
   def run_walkforward_harness(
       strategy, strategy_cfg, df, *, cost_model,
       train_months=12, test_months=3, step_months=3,
   ) -> WalkForwardResult:
   ```
   Pakai `generate_windows` dari walkforward.py; per window: `generate_signals` HANYA pada
   irisan test (dengan warmup dari ekor train — sertakan 250 bar terakhir train di df window
   supaya indikator panas, tapi BUANG sinyal yang bar_index-nya jatuh di area warmup).
   Concat OOS r_multiples → metrics OOS → gates.
2. `scripts/run_backtest.py` += flag `--walkforward`.
3. Setelah jalan untuk SEMUA kombinasi (S1×6 instrumen, S2×6), tulis ringkasan ke
   `docs/VALIDATION_RESULTS.md`: tabel per strategi×instrumen → n_trades OOS, expectancy OOS,
   PF, DSR, permutation_p, PASS/FAIL. Sertakan kesimpulan jujur: kombinasi yang FAIL ditandai
   **JANGAN DIPAKAI LIVE** (atau strategi dimatikan via strategy_state untuk instrumen itu).
   CATATAN PENTING: hasil FAIL adalah HASIL VALID — jangan menyetel parameter sampai lolos
   (itu overfitting). Laporkan apa adanya.

**BUKTI**: file `docs/VALIDATION_RESULTS.md` ada dan berisi ≥ 1 tabel hasil nyata dari data
backfill (bukan sintetis).
**Commit**: `feat(validation): walk-forward harness + official validation results (V2)`

---

## V3 — Smart-exit A/B di data nyata → keputusan champion

Jalankan `--walkforward --smart-exit` untuk kombinasi yang LOLOS V2. Bandingkan expectancy
OOS: exit baseline vs partial+BE (vs trailing bila SmartExitConfig mendukung). Tulis keputusan
di `docs/VALIDATION_RESULTS.md` section "Exit Champion": kebijakan mana yang dipakai live per
strategi. JANGAN ganti default live tanpa bukti delta positif di OOS.

**Commit**: `docs(validation): exit policy A/B results and champion decision (V3)`

---

## V4 — **[USER]** Runbook aktivasi infrastruktur

Buat file `docs/RUNBOOK_ACTIVATION.md` berisi langkah persis (agen menulis runbook,
user mengeksekusi — JANGAN commit .env):
1. **.env**: `TWELVEDATA_API_KEY`, `FINNHUB_API_KEY`, `GEMINI_API_KEY_1`,
   `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DATABASE_URL`, `REDIS_URL`.
2. **Stack**: `docker compose up -d db redis` → `uv run alembic upgrade head`.
3. **Backfill crypto dulu** (gratis & cepat):
   ```powershell
   & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol BTCUSDT --tf 1h --years 3
   # ulangi 4h; lalu ETHUSDT
   ```
   **Backfill forex/metals semalam** (rate limit 7/menit — biarkan jalan):
   XAUUSD, EURUSD, GBPUSD, USDJPY × (1h, 4h).
   Verifikasi: `SELECT i.symbol, c.timeframe, count(*) FROM candles c JOIN instruments i ON i.id=c.instrument_id GROUP BY 1,2;`
   target ≥ 18.000 baris 1h per instrumen (≈3 tahun).
4. **Kalender + VERIFIKASI MAPPING (penting)**: jalankan sync sekali
   (`python -c "import asyncio; from rtrade.pipeline.scan import sync_calendar; print(asyncio.run(sync_calendar()))"`)
   lalu cek DB: `SELECT DISTINCT currency FROM economic_events LIMIT 20;` —
   HARUS berisi kode mata uang (USD/EUR/GBP/JPY). Kalau muncul kode lain (mis. "MX", "BR"),
   tambahkan ke `_COUNTRY_TO_CURRENCY` bila relevan; kode asing non-target boleh lewat.
5. **Scan manual pertama**: `POST /scan` (lihat routes.py — butuh `API_AUTH_TOKEN`) atau
   panggil `run_scan("BTCUSDT", "1h")` via python -c. Periksa: tabel signals terisi
   (status apa pun), `signal_audits` punya row CANDIDATE/GATE, log bersih.
6. **Nyalakan LLM**: `llm.enabled: true` di settings.yaml (Gemini key wajib ada) → scan manual
   lagi → cek audit stage analyst & confidence di payload.
7. **Worker + bot**: `python -m rtrade.scheduler.main` (atau docker compose service) dan
   `python -m rtrade.cli.bot`. Tes Telegram: `/status`, `/signals`.

**Commit**: `docs(ops): activation runbook (V4)`

---

## V5 — Smoke test pasca-aktivasi (agen, setelah user menyelesaikan V4)

Checklist yang dijalankan dan dilaporkan outputnya:
- `GET /health` → semua OK.
- 24 jam pertama: tidak ada exception di log scheduler; scan tiap jam tercatat.
- Tabel `signals`: ada baris baru (published/rejected/no-signal semua sah).
- `signal_audits`: CANDIDATE/GATE/ANALYST/DELIVERY muncul sesuai alur.
- `derivatives_snapshots`: terisi untuk BTC/ETH tiap scan.
- Paper tracker: status signal berubah wajar (FILLED→TP/SL/EXPIRED) + payload
  `virtual_exits`/`excursion` terisi saat resolve.

**Commit**: `docs(ops): post-activation smoke results (V5)`

---

## V6 — Periode pembuktian paper 2–4 minggu + kriteria GO/NO-GO

Tulis di `docs/VALIDATION_RESULTS.md` section "Paper Period":
- Durasi minimal: 2 minggu ATAU 20 sinyal resolved (mana yang lebih lama).
- Review mingguan: `/calibration`, `/analytics/exits`, `/analytics/excursion`,
  `/analytics/failures` → catat.
- **Kriteria GO untuk Milestone 6 (scalping & strategi baru)**:
  (a) expectancy paper ≥ 0 setelah ≥ 20 trade, (b) tidak ada incident pipeline
  (scan gagal beruntun, data gap, halusinasi lolos), (c) kalibrasi confidence tidak
  terbalik (bucket tinggi ≥ bucket rendah dalam winrate).
- NO-GO → perbaiki akar masalah dulu; fitur baru tetap terkunci.

---

# MILESTONE 6 (TERKUNCI — jangan dikerjakan sebelum V6 GO)
Preview agar arah jelas: M15 scan path + jadwal scalping, S3 Liquidity-Sweep Reversal,
S4 VWAP scalp + order-book imbalance, volume profile levels, GR-15/16 (concurrent cap +
cooldown), champion/challenger shadow A/B framework, kalibrasi isotonic, walk-forward
param optimization + PBO grid. Task detail akan ditulis SETELAH V6 GO.

## CHECKLIST AKHIR MILESTONE 5
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests scripts
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
Test-Path docs/VALIDATION_RESULTS.md   # True
Select-String -Path scripts/run_backtest.py -Pattern "TODO" | Measure-Object  # 0
```
Laporan: per task V → status + output BUKTI + (untuk V2/V3) angka hasil validasi MENTAH.
