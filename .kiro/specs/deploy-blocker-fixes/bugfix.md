# Bugfix Requirements Document

## Introduction

Robil Trade (bot sinyal trading, signal-only) saat ini TIDAK siap deploy. Orchestrator
telah memverifikasi langsung dengan menjalankan quality gate (ruff, mypy --strict,
pytest tests/unit) dan membaca kode. Ditemukan lima kelompok cacat:

- **BUG 1 (BLOCKER):** Self-test guardrail (`run_guardrail_selftest()`) crash saat startup
  karena mencoba membangun `SignalCandidate` ilegal, padahal validasi GR-02/03/04 kini
  dijalankan saat konstruksi schema (frozen Pydantic `model_validator`). Akibatnya worker
  mati di startup setiap kali dijalankan.
- **BUG 2 (BLOCKER):** Regresi alert. Commit "implement PLAN v2" menimpa
  `scheduler/jobs.py` dan menghapus logika suppress rate-limit + cooldown 2 jam yang
  sebelumnya ada di origin/main. Akibatnya error rate-limit beruntun men-spam channel
  Telegram, dan test alert ERROR karena atribut `_last_alert_at` sudah hilang.
- **BUG 3 (TINGGI):** Penjadwalan scan TwelveData menumpuk semua instrumen pada menit "0"
  (hanya di-stagger 5 detik), sehingga di tiap pergantian jam keempat instrumen menembak
  dalam ~15 detik dan menguras bucket free TwelveData (~7 call/menit) → `RateLimitExceeded`.
- **BUG 4 (SEDANG):** mypy --strict merah (3 error) pada modul kalender, padahal dokumen
  mewajibkan mypy hijau.
- **BUG 5 (SEDANG):** `_ingest_incremental()` selalu memanggil provider walau candle
  terakhir masih segar (umur < 1 bar), memboroskan credit dan memperparah BUG 3.

Tujuan acceptance keseluruhan: semua quality gate hijau (ruff check, ruff format --check,
mypy --strict, pytest tests/unit dengan 0 failed / 0 error), worker dapat start tanpa
crash, dan tidak ada regresi pada perilaku alert. Perbaikan harus minimal dan tepat
sasaran TANPA melemahkan guardrail. Invariant proyek yang wajib dipertahankan: signal-only
(tanpa order/broker), fail-CLOSE kalender (`calendar.fail_open_when_stale=false` jangan
diubah), risk floors (GR-03 RR>=1.5, GR-04 SL in [0.5,3.0]xATR, GR-05 risk<=2%) tidak
boleh dilemahkan, GI-5 (jangan pakai `model_construct` di jalur produksi),
determinisme test (freezegun/respx, no live network), dan `llm.enabled` tetap false.

## Bug Analysis

### Current Behavior (Defect)

Perilaku salah yang terjadi saat ini.

1.1 WHEN `run_guardrail_selftest()` dipanggil (mis. dari `run_worker()` di
`src/rtrade/scheduler/main.py`) dengan kode schema saat ini THEN fungsi mencoba
membangun `SignalCandidate` ilegal di `src/rtrade/guardrails/selftest.py:53` (mis. BUY
dengan `stop_loss > entry_limit` untuk menguji GR-02), dan karena `LevelSet` serta
validator `check_direction_and_rr` di `src/rtrade/signals/schemas.py:80` memvalidasi
GR-02/GR-03/GR-04 saat konstruksi, konstruksi langsung melempar `pydantic.ValidationError`
yang tidak tertangani sehingga fungsi crash alih-alih mengembalikan `list[str]`.

1.2 WHEN `run_worker()` memanggil `problems = run_guardrail_selftest()` TANPA try/except
THEN worker mati di startup setiap kali dijalankan karena exception dari klausa 1.1 naik
ke `run_worker()` sebelum scheduler sempat start.

1.3 WHEN `scan_job()` gagal karena `RateLimitExceeded` (TwelveData 429) sebanyak >=
`_ALERT_THRESHOLD` kali berturut-turut THEN `scheduler/jobs.py` saat ini tetap mengirim
alert Telegram karena tidak ada lagi pembedaan tipe error, sehingga channel Telegram
ter-spam saat burst rate-limit.

1.4 WHEN `scan_job()` gagal karena error non-rate-limit (mis. `RuntimeError`) berkali-kali
THEN tidak ada mekanisme cooldown (`_last_alert_at` sudah dihapus dari `jobs.py`),
sehingga test referensi gagal/ERROR (`tests/unit/test_scheduler_jobs.py` fixture
`_reset_job_state` memanggil `jobs._last_alert_at.clear()` → `AttributeError`) dan
perilaku alert sekali-lalu-cooldown tidak terpenuhi.

1.5 WHEN `build_scan_schedules()` di `src/rtrade/scheduler/main.py:51` membangun jadwal
untuk instrumen TwelveData ber-TF H1 THEN semua instrumen dijadwalkan pada `minute="0"`
dengan stagger hanya 5 detik (`second` 30,35,40,45), sehingga di tiap pergantian jam
keempat scan menembak dalam ~15 detik dan menguras bucket TwelveData → `RateLimitExceeded`.

1.6 WHEN `build_scan_schedules()` membangun jadwal untuk TF H4 THEN entri H4 memakai
`minute="0"` (bukan menit terpisah), sehingga bertabrakan dengan scan H1 pada menit yang
sama dan menambah beban burst.

1.7 WHEN mypy --strict dijalankan THEN muncul error di
`src/rtrade/data/investing_calendar.py:130`: argumen `params` bertipe `dict[str, object]`
diteruskan ke `httpx.AsyncClient.get` yang tidak menerima tipe tersebut.

1.8 WHEN mypy --strict dijalankan THEN muncul error di
`src/rtrade/data/nasdaq_calendar.py:124`: argumen `params` bertipe `dict[str, object]`
diteruskan ke `httpx.AsyncClient.get` yang tidak menerima tipe tersebut.

1.9 WHEN mypy --strict dijalankan THEN muncul error di
`src/rtrade/data/nasdaq_calendar.py:169`: `_normalize_impact` dipanggil dengan argumen
bertipe `Any | None`, padahal parameter `raw_impact` mengharapkan `str | int`.

1.10 WHEN `_ingest_incremental()` di `src/rtrade/pipeline/scan.py` dipanggil sementara
candle terakhir masih segar (umur < 1 bar berjalan, mis. `ts` 09:00 dan `now` 10:00 untuk
H1) THEN fungsi tetap memanggil provider untuk fetch, memboroskan credit TwelveData dan
memperparah burst rate-limit pada BUG 3.

### Expected Behavior (Correct)

Perilaku yang seharusnya terjadi setelah perbaikan.

2.1 WHEN `run_guardrail_selftest()` dipanggil dengan kode sehat THEN fungsi SHALL
mengembalikan `list[str]` (kosong bila semua gate sehat) tanpa melempar
`pydantic.ValidationError`, dengan tetap menguji efektivitas gate GR-02/GR-03/GR-04 melalui
cara yang tidak melanggar invariant GI-5 (mis. menguji validator schema secara terpisah
dari `run_gate`, atau membatasi bypass konstruksi HANYA di dalam selftest dan tidak di
jalur produksi).

2.2 WHEN `run_worker()` menjalankan self-test saat startup dan kode dalam keadaan sehat
THEN worker SHALL melanjutkan startup tanpa crash; bila self-test menemukan masalah nyata
(mengembalikan daftar tidak kosong) worker SHALL tetap fail-closed dengan `SystemExit(1)`.

2.3 WHEN `scan_job()` gagal karena `RateLimitExceeded` berapa kali pun berturut-turut THEN
sistem SHALL TIDAK mengirim alert Telegram untuk error rate-limit tersebut, sambil tetap
mencatat `_fail_counts` (mis. `_fail_counts["USDJPY:1h"] == 4` setelah 4 kegagalan).

2.4 WHEN `scan_job()` gagal karena error non-rate-limit berkali-kali berturut-turut
(setelah mencapai threshold) THEN sistem SHALL mengirim alert Telegram tepat SEKALI lalu
menahan alert berikutnya selama periode cooldown, dengan pesan alert memuat detail error
(mis. mengandung "database unavailable"), dan atribut state `_last_alert_at` tersedia di
modul `jobs`.

2.5 WHEN `build_scan_schedules()` membangun jadwal untuk empat instrumen TwelveData ber-TF
H1 (XAUUSD, EURUSD, GBPUSD, USDJPY) THEN sistem SHALL menyebarkan jadwal per-menit
sehingga `minute` berurutan `["0","10","20","30"]` dan semua `second` bernilai `"30"`.

2.6 WHEN `build_scan_schedules()` membangun jadwal untuk TF H4 THEN entri H4 SHALL memakai
`minute == "5"` dan `hour == "0,4,8,12,16,20"`.

2.7 WHEN mypy --strict dijalankan pada `src/rtrade/data/investing_calendar.py` THEN tidak
ada error tipe pada pemanggilan `httpx.AsyncClient.get` dengan `params` (tipe `params`
SHALL diselaraskan dengan yang diterima httpx).

2.8 WHEN mypy --strict dijalankan pada `src/rtrade/data/nasdaq_calendar.py` baris
pemanggilan `get` THEN tidak ada error tipe pada `params` (tipe `params` SHALL diselaraskan
dengan yang diterima httpx).

2.9 WHEN mypy --strict dijalankan pada pemanggilan `_normalize_impact` di
`src/rtrade/data/nasdaq_calendar.py` THEN argumen yang diteruskan SHALL bertipe `str | int`
(bukan `Any | None`) sehingga tidak ada error tipe.

2.10 WHEN `_ingest_incremental()` dipanggil sementara candle terakhir masih segar (umur < 1
bar berjalan) THEN fungsi SHALL mengembalikan `0` dan TIDAK memanggil provider (tidak ada
panggilan `fetch_ohlcv`).

### Unchanged Behavior (Regression Prevention)

Perilaku yang sudah benar dan WAJIB dipertahankan.

3.1 WHEN `run_guardrail_selftest()` dieksekusi terhadap kode sehat THEN sistem SHALL
CONTINUE TO mendeteksi gate yang rusak — yakni untuk setiap kondisi ilegal yang diuji
(GR-02 sampai GR-13 dan regression check kandidat valid) tetap menghasilkan entri masalah
bila gate gagal menolaknya — sehingga `tests/unit/test_guardrail_selftest.py` tetap hijau.

3.2 WHEN sebuah `SignalCandidate` valid dibangun di jalur produksi THEN sistem SHALL
CONTINUE TO menolak input ilegal saat konstruksi via `model_validator` (GR-02/GR-03/GR-04)
dan TIDAK menggunakan `model_construct` di jalur produksi (invariant GI-5 dipertahankan).

3.3 WHEN guardrail gate mengevaluasi kandidat THEN sistem SHALL CONTINUE TO menerapkan
risk floors tanpa pelemahan: GR-03 RR>=1.5, GR-04 SL dalam [0.5,3.0]xATR, GR-05 risk<=2%.

3.4 WHEN `scan_job()` berhasil (`run_scan` tidak melempar) THEN sistem SHALL CONTINUE TO
me-reset `_fail_counts[key]` menjadi 0 dan tidak mengirim alert.

3.5 WHEN `build_scan_schedules()` dipanggil dengan dua instrumen × dua TF THEN sistem SHALL
CONTINUE TO menghasilkan tepat 4 entri jadwal (`test_all_instruments_scheduled`).

3.6 WHEN `build_scan_schedules()` membangun jadwal untuk instrumen non-TwelveData (mis.
ccxt_binance) THEN sistem SHALL CONTINUE TO men-stagger detik antar instrumen sehingga
`second` instrumen ke-0 berbeda dari instrumen ke-1 (`test_seconds_staggered`).

3.7 WHEN `_ingest_incremental()` dipanggil pada first run (tidak ada candle/`latest is
None`) THEN sistem SHALL CONTINUE TO backfill dengan `since = now - 120 hari` dan
`limit = 500` serta memanggil provider tepat sekali.

3.8 WHEN `_ingest_incremental()` dipanggil dengan candle terakhir yang sudah usang (lebih
lama dari 1 bar) THEN sistem SHALL CONTINUE TO fetch incremental dengan
`since = watermark - 2 bar` dan `limit = 10`, memanggil provider tepat sekali.

3.9 WHEN kalender non-crypto stale THEN sistem SHALL CONTINUE TO fail-CLOSE
(`calendar.fail_open_when_stale=false` tidak diubah) dan GR-07b tetap menolak sinyal.

3.10 WHEN modul kalender (`investing_calendar.py`, `nasdaq_calendar.py`) memproses respons
provider THEN sistem SHALL CONTINUE TO menghasilkan event yang sama secara fungsional
(parsing, normalisasi impact, dan penanganan error/429 tidak berubah perilakunya); hanya
anotasi/penyesuaian tipe yang berubah, bukan logika runtime.

3.11 WHEN sistem berjalan THEN sistem SHALL CONTINUE TO bersifat signal-only (tanpa
order/broker) dan menjaga `llm.enabled` tetap false, serta semua test tetap deterministik
(freezegun/respx, tanpa jaringan live).
