# IMPLEMENTATION TASKS — Instruksi Eksekusi Detail (untuk agen pelaksana)

> Dokumen ini adalah SATU-SATUNYA sumber instruksi implementasi. Konteks strategis ada di
> `docs/UPGRADE_PLAN.md` (baca section 1, 9, 10 dulu). Kerjakan task SESUAI URUTAN.
> Jangan improvisasi arsitektur. Kalau ada konflik antara dokumen ini dan kode, ikuti dokumen ini
> tapi catat konfliknya di commit message.

---

## 0. ATURAN KERJA (WAJIB DIBACA — JANGAN DILANGGAR)

### 0.1 Environment (Windows)
- `uv` TIDAK ada di PATH. Selalu pakai path lengkap:
  ```powershell
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format src tests
  & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
  ```
- Test integrasi (marker `integration`) butuh stack docker: `docker compose up -d db redis`.
  Test unit TIDAK butuh docker. Jalankan minimal unit test setiap selesai 1 task.

### 0.2 Yang DILARANG KERAS
1. **JANGAN melemahkan guardrail**: `GR03_RR_MIN_FLOOR=1.5`, `GR04_SL_ATR_MAX_CAP=3.0`,
   `GR05_RISK_PCT_CAP=2.0`, `LLM_CONFIDENCE_ADJUST_CAP=0.15`, `min_trades_for_validation>=100`.
   Nilai-nilai ini di `src/rtrade/core/config.py` TIDAK BOLEH diubah.
2. **JANGAN biarkan LLM mengubah angka** (entry/SL/TP/size). GR-10 sakral.
3. **JANGAN mengubah string value enum** di `core/constants.py` (kontrak serialisasi DB/API).
4. **JANGAN mengedit file migrasi yang sudah ada** di `migrations/versions/`. Skema baru = revisi
   alembic baru. (Untuk milestone 1 TIDAK ada perubahan skema DB — jangan buat migrasi.)
5. **JANGAN menambah dependency baru** ke `pyproject.toml` tanpa instruksi eksplisit di task.
6. **JANGAN pakai `print()`** — pakai `structlog` (ruff T20 akan menolak).
7. **JANGAN buat datetime naive** — selalu timezone-aware UTC (ruff DTZ akan menolak).
8. Semua kode baru WAJIB lolos `mypy --strict` (sudah default config) dan `ruff check`.

### 0.3 Alur kerja per task
1. Buat branch dari main: `git checkout -b feat/milestone-1` (sekali di awal, semua task di branch ini).
2. Kerjakan SATU task sampai selesai (kode + test).
3. Jalankan: `uv run ruff check src tests` → `uv run mypy` → `uv run pytest -q` (unit harus hijau;
   integration boleh skip kalau docker tidak jalan, tapi JANGAN sampai merah).
4. Commit dengan message yang sudah ditentukan di task (lihat tiap task).
5. Lanjut task berikutnya. JANGAN menggabungkan banyak task dalam satu commit.

### 0.4 Definisi selesai (Definition of Done) — berlaku untuk SEMUA task
- [ ] Kode sesuai spesifikasi task (tidak lebih, tidak kurang).
- [ ] Test baru yang diminta task ada dan hijau.
- [ ] Seluruh test unit lama tetap hijau (jumlah test tidak boleh berkurang).
- [ ] ruff + mypy bersih.
- [ ] Commit message sesuai.

---

# MILESTONE 1 — KESELAMATAN & FONDASI (kerjakan berurutan T1 → T14)

---

## T1 — Fix news blackout: mapping country→currency (KRITIS, item audit #1)

**Masalah**: Finnhub mengembalikan kode negara (`"US"`, `"GB"`) di field `country`, tapi
`check_news_blackout()` membandingkan dengan kode mata uang (`"USD"`, `"GBP"`) dari
`instruments.yaml`. Tidak pernah match → blackout mati.

**File**: `src/rtrade/data/finnhub_calendar.py`

**Langkah**:
1. Tambahkan di module level (setelah `_ALWAYS_HIGH_EVENTS`):
   ```python
   # Finnhub returns COUNTRY codes; the news filter compares CURRENCY codes.
   _COUNTRY_TO_CURRENCY: dict[str, str] = {
       "US": "USD",
       "EU": "EUR",
       "EZ": "EUR",
       "DE": "EUR",
       "FR": "EUR",
       "IT": "EUR",
       "ES": "EUR",
       "NL": "EUR",
       "GB": "GBP",
       "UK": "GBP",
       "JP": "JPY",
       "CH": "CHF",
       "CA": "CAD",
       "AU": "AUD",
       "NZ": "NZD",
       "CN": "CNY",
   }


   def _to_currency(raw: str) -> str:
       """Map a Finnhub country code to a currency code; pass through unknowns."""
       code = raw.strip().upper()
       return _COUNTRY_TO_CURRENCY.get(code, code)
   ```
2. Di `fetch_events()`, ganti baris
   `currency = row.get("country", row.get("currency", ""))` menjadi:
   ```python
   currency = str(row.get("country") or row.get("currency") or "")
   ```
   (perhatikan: `or`, bukan default-arg `get` — field bisa ada tapi kosong).
3. Di konstruksi `DomainEvent`, ganti `currency=currency.upper()` menjadi
   `currency=_to_currency(currency)`.

**Test baru** — buat file `tests/unit/test_finnhub_mapping.py`:
- `test_country_codes_map_to_currency()`: assert `_to_currency("US") == "USD"`,
  `"GB" → "GBP"`, `"DE" → "EUR"`, `"JP" → "JPY"`.
- `test_unknown_code_passthrough()`: `_to_currency("usd ") == "USD"`, `_to_currency("XX") == "XX"`.
- `test_blackout_matches_mapped_currency()`: bangun event dict
  `{"event": "Nonfarm Payrolls", "currency": "USD", "impact": "high", "event_time": <now+10min UTC>}`
  → `check_news_blackout([event], ["USD"], now)` harus `(True, reason)`.
  (import `check_news_blackout` dari `rtrade.risk.news_filter`; pakai `datetime.now(UTC)`).

**Commit**: `fix(news): map Finnhub country codes to currency codes so GR-07 blackout can match (T1)`

---

## T2 — News fail-CLOSED saat kalender basi (KRITIS, item audit #2)

**Masalah**: kalender gagal sync → tabel event kosong → sinyal forex/metals jalan TANPA proteksi.

**Langkah**:
1. `src/rtrade/persistence/repositories.py` — tambah method di `EventRepo`:
   ```python
   async def latest_fetch_ts(self) -> datetime | None:
       """Newest fetched_at across all events (None if table empty)."""
       result = await self._session.execute(select(func.max(EconomicEvent.fetched_at)))
       return result.scalar_one_or_none()
   ```
2. `src/rtrade/guardrails/gate.py` — tambah parameter baru di `run_gate()` (taruh di blok GR-07,
   setelah parameter `news_blackout_after_min`):
   ```python
   calendar_stale: bool = False,
   ```
   dan tambahkan SEBELUM blok `# --- GR-07: News blackout ---`:
   ```python
   # --- GR-07b: fail-CLOSED when the economic calendar is stale ---
   if calendar_stale:
       failures.append(
           GateFailure(
               gate_id="GR-07",
               reason="economic calendar is stale/empty — fail-closed for non-crypto",
           )
       )
   ```
3. `src/rtrade/pipeline/scan.py` — di `run_scan()`, setelah query events (`events = await EventRepo(...)`):
   ```python
   calendar_ts = await EventRepo(session).latest_fetch_ts()
   calendar_stale = instrument.market != Market.CRYPTO and (
       calendar_ts is None or (now - ensure_utc(calendar_ts)) > timedelta(hours=18)
   )
   ```
   Teruskan `calendar_stale=calendar_stale` ke `_run_strategies(...)` (tambah parameter), dan dari
   sana ke `run_gate(..., calendar_stale=calendar_stale)`.
   CATATAN: `Market` sudah diimport di scan.py. `ensure_utc` juga sudah diimport.

**Test baru** — tambah di `tests/unit/test_guardrails.py`:
- `test_gate_fails_closed_when_calendar_stale()`: buat candidate valid (tiru helper test yang sudah
  ada di file itu), panggil `run_gate(candidate, calendar_stale=True)` → `passed is False`,
  ada failure dengan `gate_id == "GR-07"`.
- `test_gate_passes_when_calendar_fresh()`: `run_gate(candidate, calendar_stale=False)` → tidak ada
  failure GR-07 (selama tidak ada events).

**Commit**: `fix(news): fail-closed gate when economic calendar is stale or empty (T2)`

---

## T3 — GR-12 hanya menghitung sinyal PUBLISHED (KRITIS, item audit #3 — akar "pelit sinyal")

**File**: `src/rtrade/persistence/repositories.py`

**Langkah**:
1. Ubah signature `SignalRepo.count_since`:
   ```python
   async def count_since(
       self,
       *,
       instrument_id: int,
       start: datetime,
       end: datetime,
       statuses: tuple[str, ...] = ("PUBLISHED",),
   ) -> int:
   ```
2. Tambah filter di query: `Signal.status.in_(statuses)` (tambahkan ke `.where(...)`).
3. `pipeline/scan.py` TIDAK perlu diubah (default sudah benar).

**Test baru** — tambah di `tests/integration/test_db_roundtrip.py` (marker integration sudah ada
di file itu, ikuti pola yang ada):
- `test_count_since_ignores_rejected()`: insert 2 Signal REJECTED + 1 PUBLISHED dengan bar_ts hari
  ini & instrument sama → `count_since(...)` harus `1`.

**Commit**: `fix(risk): GR-12 daily cap counts only PUBLISHED signals (T3)`

---

## T4 — Fix estimasi biaya LLM (KRITIS, item audit #4)

**Masalah**: `litellm.completion_cost(prompt=str(prompt_tokens), ...)` menghitung token dari string
angka, bukan biaya dari jumlah token → biaya tercatat ~0.

**File**: `src/rtrade/llm/client.py`

**Langkah**: ganti seluruh isi `_estimate_cost` dengan:
```python
def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Cost estimate from token counts (USD)."""
    try:
        from litellm import cost_per_token

        input_cost, output_cost = cost_per_token(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return float(input_cost) + float(output_cost)
    except Exception:
        # Fallback: Gemini Flash-Lite pricing (~$0.075/1M in, $0.30/1M out).
        input_cost = prompt_tokens * 0.000000075
        output_cost = completion_tokens * 0.0000003
        return input_cost + output_cost
```

**Test baru** — tambah di `tests/unit/test_llm_client.py`:
- `test_estimate_cost_uses_cost_per_token(monkeypatch)`: monkeypatch
  `litellm.cost_per_token` agar return `(0.001, 0.002)` → `_estimate_cost("x", 100, 50) == pytest.approx(0.003)`.
- `test_estimate_cost_fallback(monkeypatch)`: monkeypatch agar raise `Exception` →
  hasil = `100*7.5e-8 + 50*3e-7` (pakai `pytest.approx`).

**Commit**: `fix(llm): cost estimate uses cost_per_token with token counts (T4)`

---

## T5 — Buang bar yang belum close saat ingest (KRITIS — anti look-ahead)

**Masalah**: provider menyertakan bar yang sedang berjalan; scan jalan 30 detik setelah close →
bar umur 30 detik masuk DB dan dibaca strategi sebagai "bar closed terakhir".

**File**: `src/rtrade/data/ingestion.py`

**Langkah**: di `ingest_candles()`, SETELAH fetch berhasil dan SEBELUM `detect_gaps`:
```python
from rtrade.core.timeutil import last_closed_candle_open  # taruh di import atas file

cutoff = last_closed_candle_open(timeframe)
closed_candles = [c for c in candles if c.ts <= cutoff]
if len(closed_candles) < len(candles):
    logger.info(
        "dropped forming bars",
        symbol=instrument.symbol,
        timeframe=timeframe.value,
        dropped=len(candles) - len(closed_candles),
    )
candles = closed_candles
if not candles:
    return 0
```

**Test baru** — tambah di `tests/unit/test_data_base.py` (atau file test ingestion yang ada;
kalau belum ada test ingestion, buat `tests/unit/test_ingestion_forming_bar.py`):
- Pakai `freezegun.freeze_time("2026-06-11 10:00:35", tz_offset=0)`.
- Buat provider palsu (subclass `MarketDataProvider`) yang return 3 candle 1H:
  ts `08:00`, `09:00` (closed) dan `10:00` (forming, baru buka 35 detik).
- Buat repo palsu yang merekam rows yang di-upsert (method `upsert_many` simpan ke list, return len).
- Panggil `ingest_candles(...)` → assert hanya 2 row ter-upsert dan ts `10:00` TIDAK ada.

**Commit**: `fix(data): drop forming (unclosed) bars at ingestion — anti look-ahead (T5)`

---

## T6 — Ingestion incremental + jadwal 4H pintar (Smart Data Layer inti, §9 UPGRADE_PLAN)

**File**: `src/rtrade/pipeline/scan.py`

**Langkah**:
1. Di `run_scan()`, ganti blok ingest (yang sekarang `since = now - timedelta(days=120)` dan dua
   panggilan `ingest_candles`) dengan helper baru. Tambahkan fungsi module-level:
   ```python
   async def _ingest_incremental(
       provider: MarketDataProvider,
       instrument: InstrumentConfig,
       instrument_id: int,
       tf: Timeframe,
       repo: CandleRepo,
       now: datetime,
   ) -> int:
       """Fetch only what's missing: watermark − 2 bars overlap, tiny limit."""
       from rtrade.core.timeutil import timeframe_duration

       latest = await repo.latest(instrument_id, tf)
       if latest is None:
           since = now - timedelta(days=120)
           limit = 500
       else:
           since = ensure_utc(latest.ts) - 2 * timeframe_duration(tf)
           limit = 10
       return await ingest_candles(
           provider, instrument, instrument_id, tf, repo, since=since, limit=limit
       )
   ```
2. Pakai helper ini untuk 1H. Untuk 4H, ingest hanya jika "due":
   ```python
   candle_repo = CandleRepo(session)
   now = datetime.now(UTC)   # pindahkan deklarasi `now` ke SEBELUM blok ingest
   await _ingest_incremental(provider, instrument, inst_row.id, tf, candle_repo, now)

   if tf == Timeframe.H1 and Timeframe.H4 in instrument.timeframes:
       latest_4h = await candle_repo.latest(inst_row.id, Timeframe.H4)
       due_4h = latest_4h is None or (
           ensure_utc(latest_4h.ts) + 2 * timeframe_duration(Timeframe.H4) <= now
       )
       if due_4h:
           await _ingest_incremental(
               provider, instrument, inst_row.id, Timeframe.H4, candle_repo, now
           )
   ```
   PENTING: hapus duplikasi `now = datetime.now(UTC)` yang lama di bawah (sekarang dideklarasi
   sekali di atas); pastikan variabel `now` yang dipakai gate/news tetap ada.
3. JANGAN mengubah logika lain di run_scan pada task ini.

**Test baru** — buat `tests/unit/test_ingest_incremental.py`:
- Fake repo dengan `latest()` yang bisa diset return None / candle dengan ts tertentu, dan
  `upsert_many` perekam; fake provider perekam argumen `since`/`limit` (return []).
- `test_first_run_backfills_120d()`: latest=None → provider dipanggil dengan limit=500 dan
  since ≈ now−120d (toleransi 1 menit).
- `test_incremental_uses_watermark()`: latest.ts = 2026-06-11 08:00 UTC, tf=H1 →
  since == 06:00 UTC (watermark − 2 bar), limit == 10.

**Commit**: `feat(data): incremental ingestion with watermark + smart 4H schedule (T6)`

---

## T7 — `generate_candidate` sadar timeframe (bug valid_until)

**File**: `src/rtrade/signals/engine.py`

**Langkah**:
1. Tambah parameter `timeframe: Timeframe = Timeframe.H1` pada `generate_candidate()` (taruh
   setelah `gap_zones`, sebelum keyword-only `*`... — TIDAK: letakkan sebagai keyword-only,
   tambahkan di bawah `has_high_impact_event`).
2. Ganti blok langkah 9:
   ```python
   # 9. Compute valid_until (bar close + valid_bars × timeframe).
   from rtrade.core.timeutil import timeframe_duration  # taruh di import atas file

   tf = timeframe
   raw_ts = pd.Timestamp(df.index[-1]).to_pydatetime()
   bar_ts = raw_ts if raw_ts.tzinfo is not None else raw_ts.replace(tzinfo=UTC)
   bar_close = bar_ts + timeframe_duration(tf)
   valid_until = bar_close + valid_bars * timeframe_duration(tf)
   ```
   CATATAN PERILAKU: sebelumnya `valid_until = bar_ts(open) + 6 jam`; sekarang
   `= close + 6×TF` (lebih konsisten dengan makna "berlaku 6 bar setelah sinyal").
   Perbarui test lama yang assert nilai valid_until bila ada (cari `valid_until` di tests/).
3. `pipeline/scan.py`: panggil `generate_candidate(..., timeframe=Timeframe.H1, ...)` eksplisit.
4. Perbaiki juga komentar yang menyesatkan di `src/rtrade/signals/schemas.py` baris `bar_ts`:
   ubah komentar menjadi `# open time of the triggering bar (UTC)`.

**Test baru** — tambah di `tests/unit/test_signals.py`:
- `test_valid_until_respects_timeframe()`: jalankan generate_candidate dgn data sintetis yang
  menghasilkan kandidat (tiru fixture yang sudah ada di file test itu), `valid_bars=6`,
  timeframe=H1 → `valid_until == bar_ts + 1h + 6h`.

**Commit**: `fix(signals): valid_until derived from bar close × timeframe, tf param added (T7)`

---

## T8 — Equity & risk dari config (hapus hardcode $10.000)

**Langkah**:
1. `src/rtrade/core/config.py` — tambah field di `RiskSettings`:
   ```python
   equity_usd: float = Field(default=10_000.0, gt=0.0)
   ```
2. `config/settings.yaml` — tambah di bawah `risk:`:
   ```yaml
   equity_usd: 10000             # equity acuan sizing (ubah sesuai akun)
   ```
3. `pipeline/scan.py` — ganti `equity=10_000.0` menjadi `equity=cfg.settings.risk.equity_usd`,
   dan di pemanggilan `format_candidate_deterministic(...)` tambahkan
   `equity=cfg.settings.risk.equity_usd`.

**Test**: tambah di `tests/unit/test_config.py`:
- `test_equity_default_and_override(tmp_path)`: tulis settings.yaml minimal valid (copy dari
  fixture yang sudah ada di test config) tanpa `equity_usd` → load OK dan nilai 10000;
  dengan `equity_usd: 25000` → 25000. (Ikuti pola test config yang sudah ada di file tsb.)

**Commit**: `feat(risk): configurable account equity for sizing (T8)`

---

## T9 — Papertrack replay penuh (bukan hanya candle terakhir)

**Masalah**: tracker hanya memeriksa 1 candle terbaru tiap 15 menit → fill/SL/TP yang terjadi
beberapa candle lalu (saat tracker mati / antar-interval) terlewat.

**Langkah**:
1. `src/rtrade/papertrack/tracker.py` — tambah fungsi PURE baru (jangan ubah fungsi lama,
   masih dipakai test lama):
   ```python
   @dataclass(frozen=True)
   class CandleBar:
       """Minimal closed bar for replay."""

       ts: datetime
       high: float
       low: float


   def replay_signal(
       signal_id: str,
       action: str,
       entry_limit: float,
       stop_loss: float,
       take_profit: float,
       valid_until: datetime,
       already_filled: bool,
       candles: list[CandleBar],
   ) -> PaperTradeUpdate | None:
       """Replay closed candles in order; return the FIRST terminal update.

       Rules (worst-case, konsisten dengan backtester):
       - Belum fill: jika candle.ts > valid_until → EXPIRED.
         Jika low ≤ entry ≤ high → FILLED pada candle itu; pada candle YANG SAMA,
         jika SL juga tersentuh → langsung SL_HIT (worst case). TP pada fill bar DIABAIKAN.
       - Sudah fill: SL & TP dicek per candle; dua-duanya kena → SL first.
       """
       filled = already_filled
       fill_update: PaperTradeUpdate | None = None
       sl_dist = abs(entry_limit - stop_loss) or 1.0

       for bar in candles:
           ts = ensure_utc(bar.ts)
           if not filled:
               if ts > ensure_utc(valid_until):
                   return PaperTradeUpdate(
                       signal_id=signal_id,
                       new_status=SignalStatus.EXPIRED,
                       resolved_at=ts,
                   )
               if bar.low <= entry_limit <= bar.high:
                   filled = True
                   fill_update = PaperTradeUpdate(
                       signal_id=signal_id,
                       new_status=SignalStatus.FILLED,
                       resolved_at=ts,
                       fill_price=entry_limit,
                   )
                   # Worst-case pada fill bar: SL ikut tersentuh → langsung SL.
                   if _sl_hit(action, stop_loss, bar.high, bar.low):
                       return PaperTradeUpdate(
                           signal_id=signal_id,
                           new_status=SignalStatus.SL_HIT,
                           resolved_at=ts,
                           outcome_r=-1.0,
                       )
               continue

           sl_hit = _sl_hit(action, stop_loss, bar.high, bar.low)
           tp_hit = _tp_hit(action, take_profit, bar.high, bar.low)
           if sl_hit:
               return PaperTradeUpdate(
                   signal_id=signal_id,
                   new_status=SignalStatus.SL_HIT,
                   resolved_at=ts,
                   outcome_r=-1.0,
               )
           if tp_hit:
               return PaperTradeUpdate(
                   signal_id=signal_id,
                   new_status=SignalStatus.TP_HIT,
                   resolved_at=ts,
                   outcome_r=abs(take_profit - entry_limit) / sl_dist,
               )

       return fill_update


   def _sl_hit(action: str, stop_loss: float, high: float, low: float) -> bool:
       if action == "BUY":
           return low <= stop_loss
       return high >= stop_loss


   def _tp_hit(action: str, take_profit: float, high: float, low: float) -> bool:
       if action == "BUY":
           return high >= take_profit
       return low <= take_profit
   ```
   (import `dataclass` & `ensure_utc` sudah ada di file; tambahkan yang kurang.)
2. `pipeline/scan.py` — di `track_paper_signals()`, ganti blok per-signal:
   ```python
   for signal in await signal_repo.open_for_tracking():
       start = ensure_utc(signal.published_at or signal.bar_ts)
       rows = await candle_repo.get_range(
           signal.instrument_id,
           Timeframe(signal.timeframe),
           start,
           datetime.now(UTC),
       )
       bars = [CandleBar(ts=r.ts, high=float(r.high), low=float(r.low)) for r in rows]
       update = replay_signal(
           signal.signal_id,
           signal.action,
           float(signal.entry_limit or 0),
           float(signal.stop_loss or 0),
           float(signal.take_profit or 0),
           signal.valid_until or signal.bar_ts,
           already_filled=signal.status == SignalStatus.FILLED.value,
           candles=bars,
       )
       if update is None or update.new_status.value == signal.status:
           continue
       await signal_repo.update_tracking_status(...)
       updates += 1
   ```
   Import `CandleBar, replay_signal` dari papertrack.tracker; hapus pemakaian
   `check_fill/check_outcome` di scan (fungsi lamanya biarkan ada untuk test lama).

**Test baru** — buat `tests/unit/test_replay_signal.py` (semua candle BUY, entry=100, SL=98, TP=104,
valid_until = ts candle ke-3):
- `test_fill_then_tp`: c1 tidak sentuh; c2 low 99.5 (fill); c4 high 104.5 → TP_HIT, outcome_r=2.0,
  resolved_at = ts c4.
- `test_fill_then_sl`: fill c2; c3 low 97.9 → SL_HIT −1.0.
- `test_expired`: tidak pernah sentuh entry; candle ke-4 ts > valid_until → EXPIRED.
- `test_fill_bar_also_hits_sl_worst_case`: c2 low 97.5 (entry & SL satu bar) → SL_HIT.
- `test_both_hit_after_fill_sl_first`: fill c2; c3 low 97.9 dan high 104.5 → SL_HIT.
- `test_already_filled_continues`: already_filled=True, c1 high 104.2 → TP_HIT.

**Commit**: `fix(papertrack): full candle replay since publish — no more missed fills (T9)`

---

## T10 — Scheduler dibangun dari config + anti-burst stagger

**File**: `src/rtrade/scheduler/main.py`

**Langkah**:
1. Hapus konstanta `_SCAN_SCHEDULES` hardcode. Tambah fungsi pure:
   ```python
   def build_scan_schedules(
       instruments: list[InstrumentConfig],
   ) -> list[tuple[str, str, dict[str, str]]]:
       """One (symbol, tf, cron_kwargs) per instrument×TF; stagger seconds to avoid bursts."""
       schedules: list[tuple[str, str, dict[str, str]]] = []
       for idx, inst in enumerate(instruments):
           second = str(30 + (idx * 5) % 30)  # 30,35,40,... menghindari burst serentak
           for tf in inst.timeframes:
               if tf == Timeframe.H1:
                   cron = {"minute": "0", "second": second}
               elif tf == Timeframe.H4:
                   cron = {"minute": "0", "second": second, "hour": "0,4,8,12,16,20"}
               else:  # D1
                   cron = {"minute": "1", "second": second, "hour": "0"}
               schedules.append((inst.symbol, tf.value, cron))
       return schedules
   ```
   Import: `from rtrade.core.config import AppConfig, InstrumentConfig` dan
   `from rtrade.core.constants import Timeframe`.
2. Di `create_scheduler()`: `instruments = AppConfig.load().instruments` lalu loop hasil
   `build_scan_schedules(instruments)` (pola add_job sama persis dengan yang lama).

**Test baru** — buat `tests/unit/test_scheduler_build.py`:
- Bangun 2 `InstrumentConfig` minimal (lihat field wajib di core/config.py; isi semua field wajib)
  dengan timeframes `["1h","4h"]`.
- `test_all_instruments_scheduled()`: hasil = 4 entri (2 instrumen × 2 TF).
- `test_seconds_staggered()`: detik instrumen ke-0 ≠ detik instrumen ke-1.
- `test_4h_runs_on_4h_hours()`: entri 4h punya `hour == "0,4,8,12,16,20"`.

**Commit**: `feat(scheduler): build scan schedules from instruments.yaml with stagger (T10)`

---

## T11 — Biaya pip pakai pip_size instrumen (bug costs)

**File**: `src/rtrade/backtest/costs.py`

**Langkah**:
1. Tambah field di `CostModel`: `pip_size: float = 0.0001` (taruh setelah `symbol`).
2. `compute_trade_cost()`: ganti dua baris yang hardcode `0.0001` dengan `model.pip_size`.
3. `load_cost_models()`: baca `pip_size=float(params.get("pip_size", 0.0001))`.
4. `config/costs.yaml`: tambahkan `pip_size` per instrumen:
   `XAUUSD: pip_size: 0.01`, `EURUSD: pip_size: 0.0001`, `BTCUSDT: pip_size: 0.1`.

**Test** — tambah di `tests/unit/test_backtest.py`:
- `test_pip_cost_respects_pip_size()`: CostModel(symbol="USDJPY", pip_size=0.01,
  spread_pips_rt=2.0) → `compute_trade_cost(m, 150.0, "BUY") == pytest.approx(2.0 * 0.01)`.

**Commit**: `fix(backtest): pip-based costs use instrument pip_size (T11)`

---

## T12 — Audit trail + strategy_state + S2 news hard-block + macro 12h

**Langkah**:
1. **StrategyStateRepo** — tambah class di `src/rtrade/persistence/repositories.py`
   (import `StrategyState` dari models):
   ```python
   class StrategyStateRepo:
       def __init__(self, session: AsyncSession) -> None:
           self._session = session

       async def is_enabled(self, strategy: str) -> bool:
           row = await self._session.get(StrategyState, strategy)
           return True if row is None else bool(row.enabled)

       async def set_state(
           self, strategy: str, *, enabled: bool, reason: str | None = None
       ) -> None:
           from rtrade.core.timeutil import utcnow

           row = await self._session.get(StrategyState, strategy)
           if row is None:
               self._session.add(
                   StrategyState(
                       strategy=strategy,
                       enabled=enabled,
                       disabled_reason=reason,
                       updated_at=utcnow(),
                   )
               )
           else:
               row.enabled = enabled
               row.disabled_reason = reason
               row.updated_at = utcnow()
   ```
2. **Helper 12 jam ke depan** — tambah di `src/rtrade/risk/news_filter.py`:
   ```python
   def high_impact_within(
       events: list[dict[str, object]],
       related_currencies: list[str],
       now: datetime,
       *,
       hours: int,
   ) -> bool:
       """True jika ada event high-impact utk mata uang terkait dalam `hours` ke depan."""
       now = ensure_utc(now)
       window_end = now + timedelta(hours=hours)
       related_upper = {c.upper() for c in related_currencies}
       for event in events:
           currency = str(event.get("currency", "")).upper()
           if currency not in related_upper:
               continue
           impact = str(event.get("impact", "low")).lower()
           if impact != "high" and not _is_always_high(str(event.get("event", ""))):
               continue
           event_time = _parse_event_time(event.get("event_time"))
           if event_time is None:
               continue
           if now <= event_time <= window_end:
               return True
       return False
   ```
   Refactor parsing waktu yang duplikat dari `check_news_blackout` menjadi helper privat
   `_parse_event_time(raw) -> datetime | None` dan pakai di kedua fungsi.
3. **Wiring di scan** (`pipeline/scan.py`, dalam `_run_strategies`):
   - Di awal loop strategi:
     ```python
     state_repo = StrategyStateRepo(session_repo._session)  # JANGAN: akses privat dilarang
     ```
     ❌ JANGAN akses `_session`. Solusi benar: tambah parameter `state_repo: StrategyStateRepo`
     ke `_run_strategies`, dibuat di `run_scan` dari `session` yang sama.
   - Skip strategi yang disabled:
     ```python
     if not await state_repo.is_enabled(strategy_name):
         logger.info("strategy disabled, skipping", strategy=strategy_name)
         continue
     ```
   - S2 hard-block (berlaku generik via config strategi):
     ```python
     hard_block_h = strategy_cfg.get_int("news.hard_block_hours", 0)
     if hard_block_h > 0 and high_impact_within(
         event_dicts, instrument.related_currencies, now, hours=hard_block_h
     ):
         logger.info("news hard-block, skipping strategy", strategy=strategy_name)
         continue
     ```
   - Macro confluence diskriminatif: ganti argumen
     `has_high_impact_event=in_news_blackout` pada `generate_candidate(...)` menjadi:
     ```python
     has_high_impact_event=high_impact_within(
         event_dicts, instrument.related_currencies, now, hours=12
     ),
     ```
   - GR-13 → persist: setelah gate gagal, jika ada failure `GR-13`:
     ```python
     if any(f.gate_id == "GR-13" for f in gate.failures):
         await state_repo.set_state(
             candidate.strategy, enabled=False, reason="GR-13 negative expectancy"
         )
     ```
4. **Audit trail**: tambah parameter `audit_repo: AuditRepo` ke `_run_strategies` (dibuat di
   `run_scan`). Tulis audit di 3 titik:
   ```python
   from rtrade.core.constants import AuditStage
   # (a) kandidat terbentuk:
   await audit_repo.add(
       stage=AuditStage.CANDIDATE.value, ok=True, signal_id=candidate.candidate_id,
       detail={"symbol": instrument.symbol, "strategy": candidate.strategy,
               "confluence": candidate.confluence_score},
   )
   # (b) hasil gate (ok atau gagal):
   await audit_repo.add(
       stage=AuditStage.GATE.value, ok=gate.passed, signal_id=candidate.candidate_id,
       detail={"failures": [f"{f.gate_id}: {f.reason}" for f in gate.failures]},
   )
   # (c) delivery — lihat T13.
   ```
   CATATAN: `AuditRepo.add` signature-nya `(*, stage, ok, detail, signal_id=None)` — sesuaikan.
5. **API enable strategy** — tambah route di `src/rtrade/delivery/api/routes.py`:
   `POST /strategies/{name}/enable` dan `POST /strategies/{name}/disable` — pola auth bearer
   SAMA PERSIS dengan `/scan` (copy blok validasi authorization). Body kerja: buka session,
   `StrategyStateRepo.set_state(name, enabled=True/False, reason="manual via API")`, commit,
   return `{"strategy": name, "enabled": true/false}`.

**Test baru**:
- `tests/unit/test_news_filter.py` (tambah): `test_high_impact_within_window()` (event 6 jam ke
  depan, hours=12 → True; hours=3 → False; currency tidak terkait → False; impact low → False).
- `tests/integration/test_db_roundtrip.py` (tambah): `test_strategy_state_roundtrip()` —
  default enabled True; set_state(False, "x") → is_enabled False; set_state(True) → True.

**Commit**: `feat(pipeline): audit trail, persistent strategy state, S2 news hard-block, 12h macro (T12)`

---

## T13 — Delivery yang jujur + AlertManager wiring

**Langkah**:
1. `src/rtrade/delivery/telegram_bot.py` — `send_signal` return bool:
   ```python
   async def send_signal(self, text: str) -> bool:
       if self.is_muted:
           logger.info("signal muted, skipping Telegram delivery")
           return False
       try:
           await self._bot.send_message(chat_id=self._chat_id, text=text, parse_mode=None)
           logger.info("signal sent to Telegram")
           return True
       except Exception as exc:
           logger.error("failed to send Telegram message", error=str(exc))
           return False
   ```
2. `src/rtrade/persistence/repositories.py` — tambah di `SignalRepo`:
   ```python
   async def mark_delivery(
       self, signal_id: str, *, sent: bool, error: str | None, at: datetime
   ) -> None:
       signal = await self.get(signal_id)
       if signal is None:
           return
       payload = dict(signal.payload)
       payload["delivery"] = {
           "sent": sent,
           "error": error,
           "at": ensure_utc(at).isoformat(),
       }
       signal.payload = payload
   ```
3. `pipeline/scan.py` — blok pengiriman Telegram di `run_scan` diganti:
   ```python
   if deliver and result.message and cfg.secrets.telegram_bot_token and cfg.secrets.telegram_chat_id:
       telegram = TelegramDelivery(cfg.secrets.telegram_bot_token, cfg.secrets.telegram_chat_id)
       try:
           sent = await telegram.send_signal(result.message)
       finally:
           await telegram.close()
       async with session_factory() as session:
           repo = SignalRepo(session)
           await repo.mark_delivery(
               result.signal_id or "", sent=sent, error=None if sent else "telegram send failed",
               at=datetime.now(UTC),
           )
           await AuditRepo(session).add(
               stage=AuditStage.DELIVERY.value, ok=sent,
               signal_id=result.signal_id, detail={"sent": sent},
           )
           await session.commit()
   ```
4. `src/rtrade/scheduler/jobs.py` — bungkus scan_job dengan alert:
   ```python
   _scan_failures: dict[str, int] = {}
   _alerts: AlertManager | None = None


   def _get_alerts() -> AlertManager:
       global _alerts
       if _alerts is None:
           cfg = AppConfig.load()
           _alerts = AlertManager(
               cfg.secrets.telegram_bot_token,
               cfg.secrets.telegram_chat_id,
               enabled=bool(cfg.secrets.telegram_bot_token),
           )
       return _alerts


   async def scan_job(symbol: str, timeframe: str) -> None:
       logger.info("scan_job started", symbol=symbol, timeframe=timeframe)
       try:
           result = await run_scan(symbol, timeframe)
       except Exception as exc:
           count = _scan_failures.get(symbol, 0) + 1
           _scan_failures[symbol] = count
           logger.error("scan_job failed", symbol=symbol, error=str(exc), consecutive=count)
           await _get_alerts().alert_scan_failed(symbol, str(exc), count)
           return
       _scan_failures.pop(symbol, None)
       _get_alerts().reset_scan_failures(symbol)
       logger.info("scan_job completed", symbol=symbol, status=result.status,
                   signal_id=result.signal_id, failures=result.failures)
   ```
   (import AlertManager dari `rtrade.monitoring.alerts`.)

**Test baru**:
- `tests/unit/test_alerts.py` (tambah): `test_alert_scan_failed_threshold()` — consecutive=2 →
  return False tanpa kirim; consecutive=3 → manggil `_send_telegram` (monkeypatch jadi async
  return True) → return True. (Pola monkeypatch sudah ada di file test alerts.)
- Telegram: `tests/unit/test_delivery_status.py` baru — monkeypatch `Bot.send_message` raise →
  `send_signal` return False; sukses → True. (Buat instance TelegramDelivery dengan token dummy
  `"123:abc"`; JANGAN melakukan network call — monkeypatch method `self._bot.send_message`.)

**Commit**: `feat(ops): honest delivery status + scan failure alerts wired (T13)`

---

## T14 — TwelveData D1 parse + backfill CLI

**Langkah**:
1. `src/rtrade/data/twelvedata_provider.py` — parsing datetime dukung 2 format:
   ```python
   raw_dt = str(row["datetime"])
   try:
       ts = datetime.strptime(raw_dt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
   except ValueError:
       ts = datetime.strptime(raw_dt, "%Y-%m-%d").replace(tzinfo=UTC)
   ```
2. **Backfill CLI** — buat package `src/rtrade/cli/__init__.py` (kosong + docstring) dan
   `src/rtrade/cli/backfill.py`:
   ```python
   """Backfill historis: python -m rtrade.cli.backfill --symbol XAUUSD --tf 1h --years 3"""

   from __future__ import annotations

   import argparse
   import asyncio
   from datetime import UTC, datetime, timedelta
   from decimal import Decimal

   import redis.asyncio as aioredis
   import structlog

   from rtrade.core.config import AppConfig
   from rtrade.core.constants import Timeframe
   from rtrade.core.timeutil import timeframe_duration
   from rtrade.data.ingestion import ingest_candles
   from rtrade.data.ratelimit import RateLimiter
   from rtrade.persistence.db import create_engine, create_session_factory
   from rtrade.persistence.repositories import CandleRepo, InstrumentRepo
   from rtrade.pipeline.scan import _make_market_provider

   logger = structlog.get_logger(__name__)

   _PAGE_LIMIT = {"twelvedata": 5000, "ccxt_binance": 1000}


   async def backfill(symbol: str, tf: Timeframe, years: float) -> int:
       cfg = AppConfig.load()
       instrument = cfg.instrument(symbol)
       redis_client = aioredis.from_url(cfg.secrets.redis_url)
       limiter = RateLimiter(redis_client)
       provider = _make_market_provider(instrument, cfg, limiter)
       engine = create_engine(cfg.secrets.database_url)
       session_factory = create_session_factory(engine)
       page_limit = _PAGE_LIMIT.get(instrument.provider, 500)
       total = 0
       try:
           async with session_factory() as session:
               inst_row = await InstrumentRepo(session).get_or_create(
                   symbol=instrument.symbol, market=instrument.market.value,
                   provider=instrument.provider, provider_symbol=instrument.provider_symbol,
                   pip_size=Decimal(str(instrument.pip_size)),
                   config=instrument.model_dump(mode="json"),
               )
               repo = CandleRepo(session)
               cursor = datetime.now(UTC) - timedelta(days=int(years * 365))
               end = datetime.now(UTC)
               while cursor < end:
                   n = await ingest_candles(
                       provider, instrument, inst_row.id, tf, repo,
                       since=cursor, limit=page_limit,
                   )
                   await session.commit()
                   total += n
                   if n == 0:
                       cursor += timedelta(days=30)  # lompati lubang (mis. delisting/libur)
                   else:
                       latest = await repo.latest(inst_row.id, tf)
                       assert latest is not None
                       new_cursor = latest.ts + timeframe_duration(tf)
                       if new_cursor <= cursor:
                           break  # tidak ada kemajuan → stop
                       cursor = new_cursor
                   logger.info("backfill page", symbol=symbol, upserted=n,
                               cursor=cursor.isoformat(), total=total)
           return total
       finally:
           await provider.close()
           await redis_client.aclose()
           await engine.dispose()


   def main() -> None:
       parser = argparse.ArgumentParser()
       parser.add_argument("--symbol", required=True)
       parser.add_argument("--tf", default="1h", choices=[t.value for t in Timeframe])
       parser.add_argument("--years", type=float, default=3.0)
       args = parser.parse_args()
       total = asyncio.run(backfill(args.symbol.upper(), Timeframe(args.tf), args.years))
       logger.info("backfill complete", symbol=args.symbol, total=total)


   if __name__ == "__main__":
       main()
   ```
   CATATAN: rate limiter sudah menahan laju (TwelveData bucket 7/menit). Loop ini akan lambat
   untuk data bertahun-tahun — itu MEMANG DESAINNYA (jangan bypass limiter). RateLimitExceeded
   di-retry oleh tenacity di provider.

**Test baru** — `tests/unit/test_twelvedata_parse.py`:
- Pakai `respx` (sudah ada di dev deps) mock endpoint `/time_series` yang return values dengan
  `"datetime": "2026-06-10"` (format daily) → fetch_ohlcv(D1) menghasilkan 1 candle ts 00:00 UTC.
  (Pola respx: lihat test yang sudah ada bila ada; kalau tidak, buat `respx.mock` decorator dgn
  `httpx` route `https://api.twelvedata.com/time_series`.)
- Backfill loop TIDAK diuji end-to-end (butuh network); cukup pastikan import & argparse jalan:
  `test_backfill_cli_parses_args(monkeypatch)` — monkeypatch `backfill` jadi async return 0,
  panggil `main()` dengan `sys.argv` termock → tidak exception.

**Commit**: `feat(data): TwelveData daily parse fix + paginated backfill CLI (T14)`

---

# MILESTONE 2 — LLM PIPELINE AKTIF (T15–T17). Kerjakan SETELAH Milestone 1 hijau semua.

## T15 — Model dari config + kompresi context pack

**Langkah**:
1. `config/settings.yaml` — ganti:
   ```yaml
   analyst_model: gemini/gemini-3.1-flash-lite
   critic_model: gemini/gemini-3.1-flash-lite
   ```
   (alias litellm `trading-analyst` TIDAK akan resolve di library mode tanpa Router — pakai
   string model langsung. Ganti ke flagship nanti = edit 2 baris ini.)
2. `src/rtrade/llm/pipeline.py` — `run_llm_pipeline()` tambah kwargs:
   ```python
   analyst_model: str = "gemini/gemini-3.1-flash-lite",
   critic_model: str = "gemini/gemini-3.1-flash-lite",
   ```
   dan teruskan: `run_analyst(client, pack, model=analyst_model)`,
   `run_critic(client, pack, assessment, model=critic_model)`.
3. `src/rtrade/llm/context_pack.py` — `to_prompt_text()` versi hemat token:
   ```python
   def to_prompt_text(self) -> str:
       import json

       return json.dumps(self.to_dict(), separators=(",", ":"), default=str)
   ```
   Dan di `build_context_pack`, perketat limit: `swing_highs[:3]`, `swing_lows[:3]`,
   `sr_levels[:5]`, `gap_zones[:3]`, `calendar_events[:10]`.

**Test**: update test yang assert format prompt bila ada; tambah
`test_prompt_text_compact()` di `tests/unit/test_verifier.py` ATAU file test context pack —
assert `"\n" not in pack.to_prompt_text()` dan `"  " not in ...`.

**Commit**: `feat(llm): models from config + compact context pack (T15)`

## T16 — Integrasi LLM pipeline ke run_scan (di belakang flag llm.enabled)

**Langkah** (`pipeline/scan.py`):
1. Tambah helper module-level:
   ```python
   def _build_pack(
       instrument: InstrumentConfig,
       candidate: SignalCandidate,
       df_1h: pd.DataFrame,
       sr_levels: list[Any],
       gap_zones: list[Any],
       regime: Any,
       event_dicts: list[dict[str, object]],
       session_active: bool,
   ) -> ContextPack:
       from rtrade.indicators.engine import snapshot as indicator_snapshot

       snap = indicator_snapshot(df_1h)
       e = candidate.levels.entry_limit
       sl = candidate.levels.stop_loss
       tp = candidate.levels.take_profit
       rr = abs(tp - e) / abs(e - sl)
       swings = detect_swing_points(df_1h.tail(200))
       highs = [
           {"price": p.price, "ts": p.ts.isoformat()} for p in swings if p.is_high
       ][-3:]
       lows = [
           {"price": p.price, "ts": p.ts.isoformat()} for p in swings if not p.is_high
       ][-3:]
       return build_context_pack(
           symbol=instrument.symbol,
           market=instrument.market.value,
           timeframe=candidate.timeframe,
           session_active=session_active,
           action=candidate.action.value,
           entry=e, sl=sl, tp=tp, rr=rr,
           valid_until=candidate.valid_until.isoformat(),
           strategy=candidate.strategy,
           confluence_breakdown=candidate.confluence_breakdown.model_dump(),
           snapshot=snap,
           swing_highs=highs,
           swing_lows=lows,
           sr_levels=[
               {"price": l.price, "strength": l.strength, "is_resistance": l.is_resistance}
               for l in sr_levels
           ],
           gap_zones=[
               {"high": g.high, "low": g.low, "direction": g.direction} for g in gap_zones
           ],
           regime_state=regime.regime.value,
           regime_since=regime.since.isoformat(),
           calendar_events=[
               {**ev, "event_time": ev["event_time"].isoformat()
                if hasattr(ev["event_time"], "isoformat") else ev["event_time"]}
               for ev in event_dicts
           ],
           derivatives=None,
           df_1h=df_1h,
       )
   ```
   Import yang dibutuhkan: `ContextPack, build_context_pack` dari `rtrade.llm.context_pack`,
   `SignalCandidate` dari schemas, `run_llm_pipeline, PipelineDecision` dari `rtrade.llm.pipeline`,
   `LLMClient` dari `rtrade.llm.client`.
2. Di `_run_strategies`, SETELAH `gate.passed` true dan SEBELUM blok publish deterministik:
   ```python
   if cfg.settings.llm.enabled:
       pack = _build_pack(
           instrument, candidate, df_1h, sr_levels, gap_zones, regime,
           event_dicts, _session_active(instrument, now),
       )
       client = llm_client or LLMClient(
           api_key=cfg.secrets.gemini_api_key_1,
           timeout=cfg.settings.llm.timeout_seconds,
           temperature=cfg.settings.llm.temperature,
       )
       pres = await run_llm_pipeline(
           candidate, pack, client,
           confidence_min=cfg.settings.signal.confidence_min,
           analyst_model=cfg.settings.llm.analyst_model,
           critic_model=cfg.settings.llm.critic_model,
       )
       await audit_repo.add(
           stage=AuditStage.ANALYST.value, ok=pres.decision != PipelineDecision.ABSTAIN,
           signal_id=candidate.candidate_id,
           detail={"decision": pres.decision.value, "confidence": pres.confidence,
                   "latency_ms": pres.pipeline_latency_ms},
       )
       if pres.decision == PipelineDecision.REJECTED:
           # simpan REJECTED (pakai pola _signal_model status REJECTED yang sudah ada,
           # payload tambah {"llm": {"rationale": pres.rationale}})
           ...
           return ScanResult(..., status="rejected_llm", ...)
       if pres.decision == PipelineDecision.ABSTAIN:
           # simpan ABSTAINED (status=SignalStatus.ABSTAINED, confidence=pres.confidence)
           ...
           return ScanResult(..., status="abstained", ...)
       # PUBLISH atau FALLBACK → terbit dengan data LLM:
       confidence = Decimal(str(pres.confidence))
       rationale = pres.rationale
       key_risks = pres.key_risks
       sources = pres.sources or ["deterministic_pipeline"]
       llm_used = pres.llm_used
   else:
       confidence = Decimal(str(round(candidate.confluence_score / 100, 4)))
       rationale = "Sinyal deterministik: semua guardrail utama lolos."
       key_risks = ["Eksekusi tetap manual; validasi ulang spread dan berita sebelum entry."]
       sources = ["deterministic_pipeline"]
       llm_used = False
   ```
   lalu blok `TradingSignal(...)` yang sudah ada memakai variabel-variabel di atas
   (`confidence=float(confidence), rationale=rationale, key_risks=key_risks, sources=sources,
   llm_used=llm_used`). Pesan Telegram pakai `format_signal_from_pipeline(...)` bila
   `cfg.settings.llm.enabled` (import dari delivery.formatter), selain itu tetap
   `format_candidate_deterministic`.
3. Tambah parameter injeksi untuk test: `_run_strategies(..., llm_client: LLMClient | None = None)`
   dan `run_scan(..., llm_client: LLMClient | None = None)` yang meneruskannya.
4. JANGAN set `llm.enabled: true` di settings.yaml default (biarkan false; user yang menyalakan).

**Test baru** — `tests/unit/test_scan_llm_integration.py`:
- Buat `FakeLLMClient` dengan method `complete()` yang return `LLMCallResult` berisi JSON valid
  AnalystAssessment (verdict CONFIRM, confidence 0.8, sources = 1 source_id valid dari pack —
  trik: verdict analis pakai `sources: ["reg:state:..."]`? source_id dinamis; alternatif lebih
  stabil: mock di level `run_llm_pipeline`, bukan client. LAKUKAN INI:) —
  monkeypatch `rtrade.pipeline.scan.run_llm_pipeline` dengan async fake yang return
  `PipelineResult(decision=PUBLISH, confidence=0.7, rationale="ok", key_risks=[], sources=["s"],
  llm_used=True)`.
- Test 1 `test_llm_publish_path`: dengan `cfg.settings.llm.enabled=True` (bangun AppConfig di
  memori dari fixture test_config) → hasil scan status "published", payload signal punya
  llm_used True. (Kalau menjalankan run_scan penuh butuh DB → terlalu berat untuk unit; ALTERNATIF
  yang DIWAJIBKAN: uji `_build_pack` saja secara unit + uji mapping decision→status lewat fungsi
  kecil. Ekstrak mapping decision ke fungsi pure `_decide_publication(pres) ->
  tuple[SignalStatus, str]` dan unit-test fungsi itu: PUBLISH→PUBLISHED, FALLBACK→PUBLISHED,
  ABSTAIN→ABSTAINED, REJECTED→REJECTED.)
- Test 2 `test_build_pack_source_ids_complete`: bangun df sintetis 250 bar + candidate dummy →
  pack punya `source_ids` non-kosong, `candidate["entry_limit"] == levels.entry_limit`.

**Commit**: `feat(llm): wire Analyst→Critic→Verifier pipeline into run_scan behind llm.enabled (T16)`

## T17 — Cascade flagship (eskalasi pita ragu)

**Langkah**:
1. `core/config.py` — tambah field di `LLMSettings`:
   ```python
   flagship_enabled: bool = False
   flagship_analyst_model: str = ""
   flagship_critic_model: str = ""
   escalation_low: float = Field(default=0.48, ge=0.0, le=1.0)
   escalation_high: float = Field(default=0.63, ge=0.0, le=1.0)
   ```
2. `settings.yaml` — tambah di `llm:`:
   ```yaml
   flagship_enabled: false
   flagship_analyst_model: ""     # contoh: anthropic/claude-opus-4-8
   flagship_critic_model: ""
   escalation_low: 0.48
   escalation_high: 0.63
   ```
3. `pipeline/scan.py` — setelah `pres` pertama, jika
   `cfg.settings.llm.flagship_enabled and cfg.settings.llm.flagship_analyst_model and
   pres.decision in (PipelineDecision.PUBLISH, PipelineDecision.ABSTAIN) and
   cfg.settings.llm.escalation_low <= pres.confidence <= cfg.settings.llm.escalation_high`:
   jalankan `run_llm_pipeline` SEKALI lagi dengan model flagship; hasil kedua MENGGANTIKAN hasil
   pertama (audit kedua-duanya dengan stage ANALYST detail `{"tier": 1}` / `{"tier": 2}`).
4. API key flagship: `LLMClient(api_key=cfg.secrets.anthropic_api_key_1 ...)` jika model diawali
   `anthropic/`, `openai_api_key_1` jika `openai/` — buat helper kecil
   `_llm_key_for(model: str, secrets) -> str`. Selain itu default gemini key.

**Test**: unit test `_llm_key_for` (3 kasus) + test fungsi keputusan eskalasi pure:
ekstrak `def _should_escalate(decision, confidence, llm_cfg) -> bool` dan uji: di dalam pita →
True; di luar → False; flagship_enabled False → False; REJECTED → False (VETO tegas tidak
dieskalasi).

**Commit**: `feat(llm): two-tier cascade — flagship escalation on uncertainty band (T17)`

---

# MILESTONE 3 — SMART EXITS & GRADING (T18–T19). Setelah Milestone 2.

## T18 — Backtester: partial TP + breakeven + trailing (opsional per-parameter)

**File**: `src/rtrade/backtest/engine.py`

**Spesifikasi PERSIS** (worst-case ordering konsisten):
1. Tambah dataclass:
   ```python
   @dataclass(frozen=True)
   class ExitPolicy:
       partial_enabled: bool = False
       partial_at_r: float = 1.0      # ambil sebagian saat profit = 1R
       partial_fraction: float = 0.5  # porsi posisi yang diambil
       breakeven_after_partial: bool = True
       trail_atr_mult: float | None = None  # None = tanpa trailing (butuh kolom 'atr' di df)
   ```
2. `run_backtest(..., exit_policy: ExitPolicy | None = None)`. `None` → perilaku lama PERSIS
   (regression test lama harus tetap hijau).
3. Logika per-bar setelah fill (urutan WAJIB per bar):
   a. Cek SL pada `sl_current` (awal = stop_loss; worst-case duluan).
   b. Jika partial belum diambil dan bar mencapai `entry ± partial_at_r×risk`
      (BUY: `high ≥ entry + partial_at_r×(entry−sl)`):
      `realized_r += partial_fraction × partial_at_r`; `size_rem −= partial_fraction`;
      jika breakeven: `sl_current = entry` (berlaku MULAI BAR BERIKUTNYA — simpan flag
      `be_pending`, aktifkan di awal iterasi bar berikut; ini mencegah SL-BE kena di bar yang sama).
   c. Cek TP penuh → exit sisa posisi di TP.
   d. Trailing (jika `trail_atr_mult`): BUY `sl_current = max(sl_current, high − mult×atr_bar)`
      (pakai kolom `atr` baris bar itu; jika NaN → skip update). Berlaku mulai bar berikutnya
      juga (update SETELAH cek a–c).
   e. r_multiple akhir = `realized_r + size_rem × exit_r` di mana
      `exit_r = (exit_price − fill) / (fill − sl_awal)` untuk BUY (mirror SELL).
      PENTING: pembagi SELALU jarak SL AWAL (bukan sl_current) supaya R konsisten.
4. SL hit pada `sl_current == entry` (breakeven) → `exit_r = 0` untuk sisa posisi.

**Test baru** — `tests/unit/test_exit_policy.py` (bangun df sintetis kecil, sinyal BUY entry=100,
SL=98 → risk=2):
- `test_none_policy_backward_compat`: hasil identik run lama (bandingkan r_multiple).
- `test_partial_then_be_then_sl`: bar2 high 102 (partial 0.5 @1R), bar3 jatuh ke 98 → SL di BE:
  r = 0.5×1.0 + 0.5×0.0 = 0.5.
- `test_partial_then_tp`: bar2 high 102, bar4 high 104 (TP 2R): r = 0.5 + 0.5×2.0 = 1.5.
- `test_sl_before_partial_same_bar`: bar2 low 97.9 dan high 102.5 → SL dulu (worst case): r = −1.0.

**Commit**: `feat(backtest): ExitPolicy — partial TP, breakeven, ATR trailing (T18)`

## T19 — Grading sinyal A/B/C (jangan pelit, tetap presisi)

**Langkah**:
1. `core/config.py` — tambah model:
   ```python
   class GradeSettings(_StrictModel):
       a_min: float = Field(default=0.70, ge=0.0, le=1.0)
       b_min: float = Field(default=0.62, ge=0.0, le=1.0)
       b_risk_mult: float = Field(default=0.5, gt=0.0, le=1.0)
       c_risk_mult: float = Field(default=0.25, gt=0.0, le=1.0)
   ```
   dan field `grades: GradeSettings = GradeSettings()` di `SignalSettings`.
   `settings.yaml`: tambah blok `grades:` di bawah `signal:` dengan nilai default eksplisit.
2. Fungsi pure baru `src/rtrade/signals/grading.py`:
   ```python
   from rtrade.core.config import GradeSettings


   def grade_signal(confidence: float, cfg: GradeSettings) -> tuple[str, float]:
       """Return (grade, risk_multiplier). Floor keamanan (GR-09) tetap di gate."""
       if confidence >= cfg.a_min:
           return "A", 1.0
       if confidence >= cfg.b_min:
           return "B", cfg.b_risk_mult
       return "C", cfg.c_risk_mult
   ```
3. `pipeline/scan.py` — saat publish: hitung `grade, mult = grade_signal(float(confidence), cfg.settings.signal.grades)`;
   masukkan ke payload TradingSignal? **JANGAN ubah schema TradingSignal/SignalCandidate
   (frozen, GR-10)** — taruh di payload dict signal model:
   `payload = signal.model_dump(mode="json"); payload["grade"] = {"grade": grade, "risk_mult": mult,
   "scaled_size": round(candidate.position_size * mult, 4)}`.
4. `delivery/formatter.py` — `format_signal_telegram` tambah parameter opsional
   `grade: str | None = None, scaled_size: float | None = None`; jika ada, sisipkan baris
   `f"Grade       : {grade}  ·  ukuran disarankan {scaled_size}"` setelah baris Confidence.
   Scan meneruskannya.

**Test**: `tests/unit/test_grading.py` — 0.75→("A",1.0); 0.65→("B",0.5); 0.56→("C",0.25);
batas tepat a_min → A. Formatter: message mengandung "Grade" saat diberi grade.

**Commit**: `feat(signals): A/B/C grading with risk scaling — more signals, same safety (T19)`

---

# SETELAH MILESTONE 3
STOP. Laporkan hasil (jumlah test, hal yang menyimpang dari rencana). Task lanjutan
(M5/M15 scalping, replay 1m, virtual exit ensemble, HMM shadow, meta-label gate, Bayesian Kelly,
permutation test, case-based memory) akan dibuatkan dokumen task terpisah setelah milestone ini
diverifikasi.

## Checklist verifikasi akhir (jalankan sebelum lapor selesai)
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff format --check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
docker compose up -d db redis; & "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q -m integration
```
Semua hijau → buat ringkasan per-task: apa yang diubah, test apa yang ditambah, deviasi (jika ada).
