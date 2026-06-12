# Robil Trade — Master Upgrade Plan (v1)

> Hasil review menyeluruh kode (2026-06-11). Tujuan: presisi scalping & swing lebih tinggi,
> winrate naik lewat filter + kalibrasi + eksekusi level yang lebih cerdas, siap model AI flagship.
> Prinsip tetap: deterministik dulu, LLM tidak pernah menyentuh angka (GR-10), abstain itu senjata.

---

## 1. Kesimpulan Review (kondisi nyata sekarang)

### Alur runtime (pipeline/scan.py)
```
scheduler (1H/4H +30s) → ingest candles (ccxt/TwelveData, 120 hari)
→ indicators (EMA21/50/200, RSI, ATR, ADX, MACD, BB, VWAP, ATR-pctile)
→ regime RULE-BASED (ADX/ATR-pctile + hysteresis)
→ structure (fractal swings → S/R cluster 0.25×ATR, gap >0.5×ATR)
→ strategy S1 (TREND) / S2 (RANGE) → levels (RR≥1.5, SL 0.5–3×ATR, tick rounding)
→ edge-quality filter → confluence ≥60 → sizing 1% fixed (equity HARDCODE $10k)
→ gate GR-01..13 → simpan DB → Telegram (deterministik)
```

### Aset yang SUDAH ADA tapi DORMAN (tidak dipanggil runtime)
| Aset | File | Status |
|---|---|---|
| LLM pipeline Analyst→Critic→Verifier + confidence formula | `llm/pipeline.py` | tidak dipanggil `run_scan`; `llm.enabled: false` |
| Context pack + source_id anti-halusinasi | `llm/context_pack.py` | tidak pernah dibangun di runtime |
| HMM regime detector | `regime/hmm.py` | tidak di-wire |
| Meta-labeling XGBoost (triple-barrier) | `ml/meta_label.py` | tidak di-wire |
| Funding rate + OI provider | `data/ccxt_provider.py` | `funding_extreme=False` hardcode di scan |
| Spread → edge-quality EQ-02 | `signals/edge_quality.py` | `spread=None` di scan → check mati |
| Kelly sizing + risk modul | `risk/sizing.py` | engine pakai inline sizing sendiri |
| KeyManager rotasi multi-key | `llm/key_manager.py` | tidak dipakai LLMClient |

### Bug / kelemahan yang ditemukan
1. `backtest/costs.py:49` — konversi pip hardcode `0.0001` → salah untuk USDJPY (0.01) & XAUUSD; harus pakai `pip_size` instrumen.
2. `signals/engine.py:151-153` — `valid_until = bar_ts + hours(valid_bars)` & `tf` hardcode H1; rusak saat TF lain ditambah.
3. `signals/schemas.py:71` — komentar `bar_ts` bilang *close time*, padahal engine mengisi *open time* (konsisten dgn `is_candle_fresh`, tapi komentar menyesatkan).
4. `pipeline/scan.py:333` — equity hardcode 10_000; harus dari config/env.
5. `papertrack` cek hanya 1 candle terakhir tiap 15 menit → bisa lolos fill/SL intra-gap; harus replay semua candle sejak last check.
6. Backtester memproses trade per-sinyal, bukan kronologis fill → equity compounding sedikit bias (minor, dokumentasikan).
7. `_session_active` kasar (07–21 UTC); tidak bedakan London/NY/overlap.
8. Walk-forward belum optimasi parameter di train window (stub) → DSR/PBO belum bermakna penuh.

### Penilaian umum
Fondasi sangat bagus (langka di proyek retail): guardrail keras di config-load, schema frozen,
anti-look-ahead, DSR/PBO, audit trail, verifier deterministik. Masalah utama: **mesin canggihnya
banyak yang belum disambungkan**, belum ada exit engineering (TP fix 2R), belum ada scalping nyata
(TF minimum 1H), dan belum ada loop kalibrasi winrate.

---

## 2. Master Plan — 5 Fase

### FASE A — "Nyalakan mesin yang sudah dibeli" (dampak instan, risiko rendah)
- **A1. Integrasi LLM pipeline ke `run_scan`** di belakang `llm.enabled`:
  build ContextPack → `run_llm_pipeline` → GR-09/10/11 → simpan assessment/critic/verifier ke `signal_audits`.
  Gemini Flash-Lite untuk testing; alias litellm tetap (flagship tinggal ganti YAML).
- **A2. Wire derivatives (crypto)**: funding rate + OI delta → `funding_extreme` ke confluence,
  + masuk context pack. Simpan snapshot ke `derivatives_snapshots` tiap scan.
- **A3. Live spread → edge-quality**: ambil bid/ask (ccxt ticker / TwelveData quote) → EQ-02 aktif.
- **A4. HMM regime — shadow mode**: klasifikasi paralel dgn rule-based, log perbandingan ke audit,
  evaluasi 4 minggu → ADR-013 diisi data nyata.
- **A5. Meta-label gate (GR-14, shadow dulu)**: train XGBoost dari trade backtest + paper,
  log `p(TP-before-SL)` di tiap sinyal; setelah ≥100 sampel & improvement OOS positif → aktif sebagai gate p≥0.58.
- **A6. Perbaiki bug §1**: costs pip_size, valid_until per-TF, equity configurable, papertrack replay,
  KeyManager → LLMClient.

### FASE B — Presisi & winrate engine (ide yang jarang dipakai retail)
- **B1. Smart Exit Engineering** (pendongkrak winrate terbesar):
  - TP struktural: target = S/R berlawanan terdekat − 0.3×ATR (bukan fix 2R) — "keluar sebelum dinding".
  - Partial TP 50% @1R + SL→breakeven; sisanya trail Chandelier (3×ATR dari extreme).
  - Semua disimulasikan dulu di backtester (extend engine: partial fill + trailing) — adopsi hanya jika expectancy OOS naik.
- **B2. S3 Liquidity-Sweep Reversal** (strategi baru): sweep swing high/low (stop-hunt) lalu close
  kembali ke dalam range dalam ≤2 bar + RSI divergence → entry limit di retest, SL di balik wick sweep.
  Setup winrate tinggi di XAUUSD & crypto; sepenuhnya deterministik & terukur.
- **B3. Volume Profile levels**: POC / HVN / LVN dari 20 hari (volume-by-price binning) →
  komponen `structure` confluence + kandidat TP. Lebih kuat dari cluster swing murni.
- **B4. MAE/MFE analytics**: dari paper trades, hitung Maximum Adverse/Favorable Excursion per strategi
  → optimasi data-driven multiplier SL (mis. SL 1.2×ATR ternyata cukup vs 2×) & TP. Edge-ratio dashboard.
- **B5. Time-of-day prior (Bayesian)**: beta-binomial winrate per (strategi × jam UTC × instrumen)
  dari outcome paper → filter jam buruk + bobot confluence. Session matrix London/NY/Asia yang presisi.
- **B6. News-surprise score**: data `actual/forecast/previous` SUDAH tersimpan di DB —
  z-score kejutan → (a) blackout adaptif (kejutan besar = blackout lebih lama),
  (b) sinyal post-news drift (15–60 mnt setelah event, arah sesuai surprise) sebagai strategi S5 eksperimental.
- **B7. Cross-asset context**: DXY (TwelveData) untuk XAUUSD/EURUSD/GBPUSD, BTC untuk ETH →
  skor korelasi/divergensi masuk confluence `macro` + context pack LLM.

### FASE C — Scalping engine sungguhan
- **C1. Timeframe M5/M15**: extend `Timeframe` enum, scheduler tick + ingestion;
  scalping crypto 24/7 (data Binance gratis), XAUUSD hanya overlap London/NY.
- **C2. S4 Scalp VWAP+Sweep (M5)**: deviasi >1.5σ dari session-anchored VWAP + micro liquidity sweep
  → mean reversion ke VWAP. Spread guard ketat (spread/ATR < 0.08), TTL 30–45 menit,
  RR tetap ≥1.5 (GR-03 dipertahankan — jangan dilonggarkan).
- **C3. Order-flow proxies (Binance public, gratis)**: taker buy/sell ratio, OI delta 1h, funding —
  "squeeze score" (OI naik + funding ekstrem + harga stagnan = bahan bakar squeeze) → arah scalp.
- **C4. Guardrail scalping baru**: GR-15 max trade aktif bersamaan, GR-16 cooldown setelah 2 SL beruntun
  per instrumen (anti revenge-signal).

### FASE D — Flagship-AI ready (Claude Opus / GPT / dst.)
- **D1. Model tiering via litellm.yaml**: alias tetap `trading-analyst/critic`; profil `testing`
  (gemini lite) vs `flagship` (Claude/GPT) — ganti 1 env var. Cap `max_confidence_adjust ±0.15` tetap.
- **D2. Case-Based Memory (jarang ada yang punya)**: k-NN retrieval setup historis serupa
  (fitur: confluence breakdown, regime, RSI/ADX/ATR-pctile, jam) + outcome nyata →
  ditambahkan ke context pack: *"12 setup termirip historis: 8 TP, 3 SL, 1 expired (WR 67%)"*.
  LLM flagship bisa reasoning di atas evidence nyata, bukan vibes. Sumber: tabel signals sendiri.
- **D3. Self-consistency voting (flagship only)**: analyst disampling n=3 (temp 0.5),
  majority verdict + mean confidence; Gemini lite tetap 1 call (hemat).
- **D4. Kalibrasi confidence**: isotonic regression `confidence → winrate empiris` dari paper outcomes;
  reliability diagram di endpoint `/calibration`; threshold GR-09 dinaikkan otomatis bila overconfident.
- **D5. Model skill scoreboard**: tiap model diminta `p(TP-before-SL)`; dinilai Brier score versus outcome →
  pilih model berdasarkan skill terukur per instrumen, bukan nama besar.

### FASE E — Validasi, anti-overfit, proteksi profit
- **E1. Walk-forward optimization nyata**: grid search parameter HANYA di train window →
  matrix returns per kombinasi → PBO dari grid (sekarang baru placeholder), DSR pakai n_trials jujur.
- **E2. Champion/Challenger (shadow A/B)**: setiap perubahan strategi/exit jalan paralel sebagai
  sinyal status `SHADOW` (tidak dikirim) → promosi hanya jika expectancy paper > champion di ≥50 trades.
  Tidak ada lagi "ganti parameter karena feeling".
- **E3. Equity-curve risk throttle**: rolling 10-trade expectancy < 0 → risk_pct ×0.5;
  pulih → naik bertahap; selalu ≤ GR-05. Anti-drawdown spiral.
- **E4. Telegram dua arah + laporan**: `/status /signals /pause /resume`, notifikasi FILLED/TP/SL,
  laporan mingguan otomatis (WR, expectancy, calibration, biaya LLM).

---

## 3. Urutan eksekusi yang disarankan
1. **A6 (bug) → A1 (LLM on) → A3 (spread) → A2 (derivatives)** — 1 sprint, semua infra sudah ada.
2. **B1 (smart exits) + extend backtester** — pendongkrak winrate terbesar, wajib lewat gate validasi.
3. **B2 (S3 sweep) + B3 (volume profile)** — strategi baru high-winrate.
4. **A4/A5 shadow (HMM, meta-label) + D4 (kalibrasi)** — loop belajar dari data sendiri.
5. **C (scalping M5/M15)** — setelah eksekusi & exits terbukti di 1H.
6. **D2/D3/D5** — saat ganti ke model flagship.
7. **E1/E2** — berjalan terus sebagai budaya validasi.

## 4. Kejujuran statistik (penting)
Winrate tinggi TIDAK bisa dijamin oleh sistem mana pun; yang bisa dilakukan sistem ini:
(1) menolak trade berkualitas rendah lebih agresif (edge-quality, meta-label, conformal abstain),
(2) memindahkan TP/SL ke tempat yang secara historis lebih sering tercapai (smart exits, MAE/MFE),
(3) mengkalibrasi confidence supaya angka 0.7 benar-benar berarti ~70%.
Semua perubahan wajib lolos gate §8.11.4 (expectancy OOS > 0 after costs, PF ≥ 1.15, DSR ≥ 0.90, PBO ≤ 0.30).

---

## 5. Auth Provider — OAuth vs API Key (kejujuran teknis)

### Masalah dengan OAuth subscription (Codex / Claude / Gemini / opencode login)
Tools itu (Claude Code, Codex CLI, gemini-cli, opencode) login pakai **OAuth langganan konsumen**.
Memakai token itu untuk **backend bot 24/7** punya 3 masalah serius:

1. **Melanggar ToS** — langganan ChatGPT/Claude Pro/Gemini ditujukan untuk pemakaian interaktif oleh
   manusia, bukan menjadi mesin di balik produk lain. Kode ini **sudah melarangnya sendiri**:
   `core/config.py` menolak prefix `sk-ant-oat` (PLAN §14.2). Risiko nyata: **akun langganan di-banned**
   → Anda kehilangan akses sama sekali, bukan cuma bot mati.
2. **Rate limit salah bentuk** — limit langganan dirancang untuk sesi interaktif, bukan scheduler yang
   nembak tiap candle close. Token OAuth juga rotasi/expire → bot gampang putus di tengah jam pasar.
3. **Tidak ada SLA & tidak stabil** — flow OAuth CLI berubah-ubah; bukan fondasi untuk uang sungguhan.

**Kesimpulan:** OAuth langganan = pilihan buruk untuk bot, terlepas dari ToS. Jangan diwire ke scheduler.

### Yang BENAR: abstraksi "Credential Provider" (tetap fleksibel, tetap legal)
Pertahankan pola litellm alias yang sudah ada, tambah lapisan provider auth pluggable:

| Mode | Untuk apa | Status legal |
|---|---|---|
| `api_key` | Anthropic/OpenAI/Gemini API key resmi (yang Gemini Lite Anda pakai sekarang) | ✅ direkomendasikan |
| `cloud_oauth` | **OAuth2 enterprise** — GCP Vertex AI (service account) / AWS Bedrock (IAM). Ini OAuth "beneran" yang memang untuk server | ✅ legal & stabil |
| `local_gateway` | Arahkan litellm ke proxy lokal (LiteLLM proxy / self-host) untuk pooling key & cache | ✅ |

Jadi keinginan "login fleksibel multi-provider" tetap terpenuhi — lewat jalur yang tidak bikin akun
kena banned. Ganti provider = ganti 1 profil di `litellm.yaml` + 1 env var (lihat D1).

### Reframe penting: yang Anda kira butuh OAuth, sebenarnya butuh ARSITEKTUR
Anggapannya: "OAuth langganan = akses flagship murah/gratis." Faktanya yang bikin flagship murah
**bukan OAuth, tapi cascade + caching** (§6). Dengan API key resmi + arsitektur di bawah, Anda dapat
kekuatan Opus/GPT-5.5 dengan biaya ~10× lebih kecil — tanpa risiko ban.

---

## 6. Efisiensi Token X10 (hasil maksimal, biaya minimal)

Prinsip: **model mahal hanya dipakai untuk keputusan yang benar-benar sulit.** Mayoritas kandidat itu
jelas bagus atau jelas jelek — itu tidak butuh flagship, apalagi voting.

### 6.1 Cascade / eskalasi bertingkat (lever terbesar)
```
Tier 0  Deterministik (confluence + edge-quality + meta-label)   → GRATIS, buang mayoritas bar
Tier 1  Gemini Lite: Analyst + Critic (2 call)                    → murah, jalan di tiap kandidat lolos
Tier 2  Flagship (Opus/GPT) — HANYA jika Tier 1 "ragu"            → ~20-30% kandidat saja
        - trigger: confidence di pita ragu (mis. 0.48–0.63), ATAU setup high-value
        - di sinilah baru self-consistency voting n=3 dijalankan
```
Efek: voting flagship yang mahal cuma jalan di ~1 kandidat/hari, bukan tiap sinyal.
Estimasi kasar: biaya LLM per sinyal turun dari ~$0.30–0.50 (voting penuh tiap kandidat, prompt tak
dikompres) → **~$0.03–0.06**. ±10× lebih murah, DAN keputusan borderline justru dapat scrutiny LEBIH banyak.

### 6.2 Prompt caching (diskon ~90% input token berulang)
System prompt Analyst/Critic = statis → cache. Metadata instrumen = statis → cache. Hanya context pack
yang berubah tiap bar. Anthropic & OpenAI dua-duanya support prompt caching → bayar penuh sekali, sisanya diskon.

### 6.3 Kompres context pack (~50-70% lebih kecil)
`context_pack.to_prompt_text()` sekarang `json.dumps(indent=2)` + daftar 10 S/R & 5 swing dengan
source_id verbose. Perbaikan: buang indentasi, kunci pendek, hanya indikator relevan untuk strategi itu,
ringkas struktur (jarak ke S/R terdekat, bukan list panjang). Token prompt bisa jadi ~1/3.

### 6.4 Voting cerdas (bukan voting buta)
- **Analyst + Critic SUDAH 2 perspektif** (konfirmasi vs adversarial). Untuk rutin, itu cukup.
- Self-consistency n=3 **hanya** saat keputusan benar-benar di pita ragu. Kalau call tunggal bilang
  0.85 CONFIRM atau 0.30 VETO → sudah tegas, tidak perlu voting.
- **Brier-score scoreboard (D5)**: kalau Gemini Lite terbukti terkalibrasi baik untuk instrumen tertentu,
  pakai terus; flagship hanya di mana ia terbukti menambah skill. Bayar mahal hanya yang terbukti berharga.

### 6.5 Reuse hasil (idempotency)
Dedup (instrument, tf, strategi, bar_ts) sudah ada → re-scan bar yang sama TIDAK panggil LLM ulang,
pakai assessment tersimpan.

---

## 7. "Jangan pelit sinyal tapi tetap presisi" (X10 thinking)

Ini soal trade-off precision/recall. Cara menaikkan JUMLAH sinyal tanpa menurunkan kualitas —
**tanpa pernah melemahkan guardrail keras** (GR-03 RR≥1.5, GR-04 SL bound, GR-05 risk cap tetap mutlak):

1. **Lebih banyak strategi ortogonal** (S3 sweep, S4 scalp, post-news drift) = lebih banyak "tembakan",
   tiap tembakan tetap presisi sendiri-sendiri. Cara paling bersih menambah sinyal.
2. **Sinyal bertingkat A/B/C** (ganti publish/reject biner):
   - **Grade A** (conf tinggi) → size penuh, label "strong".
   - **Grade B** (sedang) → size ½, label "scale-in / watch".
   - **Grade C** (spekulatif, di atas floor keamanan) → "info only", size kecil/0.
   Setup borderline tidak dibuang (tidak pelit) tapi jelas dilabeli & dikecilkan (tetap aman).
3. **Confidence → ukuran posisi, bukan gerbang biner.** Pertahankan floor keamanan mutlak (mis. 0.45),
   tapi di atasnya confidence memetakan size secara kontinu (dalam batas GR-05). Sinyal lemah tetap
   muncul, tapi kecil.
4. **Multi-timeframe** (M15 + 1H + 4H) = ~3× bar kandidat, masing-masing tetap difilter penuh.
5. **Smart exits bikin kita BERANI ambil lebih banyak** (kontra-intuitif): partial TP @1R + SL→breakeven
   berarti sinyal "salah" yang sempat +1R lalu balik = biaya nol. Proteksi exit ini membuat bar entry
   boleh sedikit lebih rendah pada Grade B/C tanpa menaikkan risiko nyata.
6. **Abstain hanya untuk ambiguitas, bukan kemediokeran** — kalibrasi ulang supaya ABSTAIN dipakai saat
   bukti benar-benar bertabrakan, bukan sekadar "kurang meyakinkan".

> Catatan jujur: tidak ada sistem yang bisa menjamin winrate tinggi. Yang sistem ini lakukan:
> (a) menolak trade jelek lebih agresif, (b) memindah TP/SL ke tempat yang historis lebih sering kena,
> (c) mengkalibrasi confidence supaya "0.70" benar-benar berarti ~70%. Lebih banyak sinyal yang
> *terkalibrasi* > sedikit sinyal yang overconfident.

---

## 8. Inovasi Gelombang 2 — "X10 Massive" (jarang diketahui, semua implementable)

Empat klaster. Tiap ide dipilih karena: jarang ada di bot retail, dasar statistiknya kuat,
bisa dibangun dengan infra yang SUDAH ada (DB candle, papertrack, audit, meta-label), dan
berdampak langsung ke winrate/presisi/biaya.

### Klaster I — Kebenaran Data (fondasi winrate yang jujur)

- **I1. Replay intra-bar 1-menit (game changer terselubung).**
  Backtester & papertrack sekarang memakai asumsi *worst-case SL-first* saat SL dan TP tersentuh
  di bar yang sama → winrate nyata Anda **UNDERSTATED** sekarang. Ingest candle 1m (gratis di
  Binance; TwelveData punya 1m) hanya untuk *resolusi* trade: urutan high/low diketahui pasti →
  SL-vs-TP-first dijawab fakta, bukan pesimisme. Efek: pengukuran winrate naik ke angka jujurnya,
  dan exit engineering (B1) bisa dioptimasi terhadap kebenaran, bukan asumsi.
  Bonus fitur dari 1m: *path efficiency* (jarak close-to-close ÷ total jalan 1m — efisiensi trend
  intrabar) dan *wick order* (high dulu atau low dulu) sebagai fitur meta-label.

- **I2. Live spread recorder → cost model yang mengkalibrasi diri.**
  Setiap scan, catat bid/ask spread ke DB. Sebulan kemudian `costs.yaml` tidak lagi tebakan statis —
  ia dihitung dari distribusi spread yang ANDA ukur sendiri per jam per instrumen. Backtest jadi
  realistis; edge-quality EQ-02 dapat threshold per-jam (spread XAU jam Asia ≠ jam London).

- **I3. Alpha-decay / waktu-paruh sinyal.**
  Ukur bagaimana expectancy meluruh terhadap jarak (bar) antara sinyal dan fill dari data paper
  sendiri → `valid_bars` per strategi ditentukan data ("sinyal S1 basi setelah 4 bar, S3 setelah 2"),
  bukan angka 6 yang seragam.

### Klaster II — Statistik Tingkat Lanjut (senjata yang hampir tak ada di retail)

- **II1. Bayesian Kelly (shrunk Kelly).**
  Masalah Kelly klasik: winrate dari sampel kecil = overbet. Solusi: posterior Beta(α=wins+1, β=losses+1)
  → pakai **batas bawah kredibel 25%** dari distribusi winrate untuk Kelly, bukan mean.
  Sampel sedikit → otomatis konservatif; sampel banyak & bagus → size naik dengan izin statistik.
  Drop-in upgrade untuk `risk/sizing.py` yang sudah punya ¼-Kelly.

- **II2. Conformal prediction di atas meta-label (jaminan distribusi-bebas).**
  Split-conformal wrapper pada probabilitas XGBoost → ambang abstain yang menjamin secara matematis:
  *"dari sinyal yang DIAMBIL, winrate ≥ X% dengan confidence 90%"* — tanpa asumsi distribusi apa pun.
  Ini cara akademis-modern (2020-an) untuk "pelit yang presisi": bukan menebak threshold, tapi
  menurunkannya dari jaminan coverage. Hampir tidak ada bot retail yang punya ini.

- **II3. Permutation test untuk setiap strategi ("kalahkan keberuntungan dulu").**
  Sebelum strategi boleh hidup: acak waktu entry 1.000× (jumlah & durasi trade sama, timing acak) →
  distribusi expectancy keberuntungan → strategi wajib p-value < 0.05. Ini White's Reality Check
  versi praktis. Gate anti-self-deception paling brutal dan paling murah (numpy saja).

- **II4. Monte Carlo bootstrap → risk-of-ruin & "lisensi size".**
  Bootstrap blok dari R-multiples paper → distribusi maxDD & P(ruin). Risk throttle (E3) jadi
  berbasis statistik: risk_pct boleh naik HANYA jika P(maxDD > 15%) < 5% pada 1.000 simulasi.

- **II5. BOCPD — Bayesian Online Changepoint Detection untuk CRISIS.**
  Deteksi CRISIS sekarang = ATR percentile ≥95 (lambat, menunggu vol sudah meledak). BOCPD
  (Adams & MacKay) mendeteksi *patahan struktur* return secara online beberapa bar lebih awal —
  ±100 baris numpy. Dipasang sebagai detektor CRISIS ketiga: rule-based ∨ HMM ∨ BOCPD (voting).

- **II6. Fitur memori-panjang: fractional differentiation + Hurst.**
  FFD (López de Prado) membuat seri harga stasioner TANPA membuang memori trend → fitur premium
  untuk meta-label & HMM. Hurst exponent rolling (H>0.55 trend, H<0.45 mean-revert) jadi *voter
  ketiga* regime — S1 hanya jalan saat Hurst setuju trend, S2 saat setuju mean-revert.

### Klaster III — Eksekusi & Portofolio

- **III1. Virtual exit ensemble (satu entry, N exit bayangan).**
  Setiap paper trade yang fill, tracker menjalankan 4-5 kebijakan exit secara paralel virtual:
  fixed 2R / TP struktural / partial+BE / chandelier / time-stop. Satu entry = lima titik data exit.
  Dalam 100 trade Anda punya 500 observasi exit → pilih kebijakan exit champion per strategi×regime
  dengan data, TANPA biaya LLM tambahan. (Sinergi langsung dengan I1.)

- **III2. GR-17: Portfolio correlation gate.**
  EURUSD BUY + GBPUSD BUY aktif bersamaan = satu taruhan USD ukuran dobel terselubung. Gate baru:
  total "exposure ekuivalen" per mata uang/aset dasar dibatasi; sinyal kedua yang berkorelasi >0.7
  diturunkan size-nya atau ditandai "correlated — pilih salah satu".

- **III3. Order-book depth imbalance (Binance L2 gratis).**
  Rasio kedalaman bid/ask ±0.5% dari mid → konfirmasi mikro untuk scalp crypto (C2): mean-reversion
  long hanya jika dinding bid lebih tebal. Satu snapshot REST per scan, tanpa websocket dulu.

- **III4. Lead-lag cross-asset prior.**
  Cross-correlation lag-1 yang diukur ulang mingguan: BTC memimpin ETH, DXY memimpin XAU/EUR →
  prior arah kecil (±5 poin confluence macro) saat pemimpin sudah bergerak dan pengikut belum.

### Klaster IV — AI yang Belajar Sendiri (flagship-ready)

- **IV1. Distilasi "flagship mengajari lite" via retrieval.**
  Setiap verdict Tier-2 (Opus/GPT) disimpan sebagai *exemplar terkurasi* (context pack ringkas +
  verdict + outcome nyata). Analyst Tier-1 (Gemini lite) menerima 3 exemplar termirip sebagai
  few-shot. Hasil: model murah mewarisi pola penilaian model mahal, biaya marginal nol.
  Makin lama dipakai, makin pintar Tier-1, makin jarang eskalasi Tier-2 → biaya turun sendiri.

- **IV2. Strategy Factory malam hari — evolusi DI DALAM sangkar.**
  Tiap malam: mutasi kecil parameter strategi (±10-20% di sekitar nilai hidup) dievaluasi HANYA di
  train window walk-forward → kandidat juara wajib lolos DSR ≥0.90, PBO ≤0.30, permutation test (II3),
  lalu shadow A/B (E2) ≥50 trade sebelum boleh menggantikan champion. Bot yang memperbaiki dirinya
  sendiri — tapi dikurung 4 lapis gate supaya jadi mesin evolusi, bukan mesin overfit.

- **IV3. LLM Coroner — otopsi otomatis setiap SL.**
  Setiap SL_HIT memicu 1 call murah: LLM membaca context pack tersimpan + jalur harga sesudahnya →
  klasifikasi sebab kematian ke taksonomi tetap (false-breakout / news-spike / regime-flip /
  sl-terlalu-ketat / fill-buruk). Agregat mingguan = peta kelemahan per strategi yang langsung
  actionable ("40% SL S1 di XAU = sl-terlalu-ketat saat ATR-pctile <30" → perbaiki satu parameter).
  Biaya: ~1 call lite per loss. Nilai: loop perbaikan tertarget, bukan tebak-tebakan.

### Urutan nilai (kalau harus memilih)
1. **I1 replay 1m** — meluruskan pengukuran winrate SEMUA komponen lain. Kerjakan duluan.
2. **III1 virtual exit ensemble** — 5× data exit gratis, sinergi dengan I1.
3. **II3 permutation test** — murah, brutal, melindungi dari self-deception selamanya.
4. **II1 Bayesian Kelly + II4 Monte Carlo** — size naik hanya dengan izin statistik.
5. **IV3 coroner + IV1 distilasi** — loop belajar; **II2 conformal** saat sampel ≥200; **IV2 factory** paling akhir.

---

## 9. Smart Data Layer — backfill sekali, lalu incremental saja

### Masalah di kode sekarang (boros & berisiko)
1. **Re-fetch besar tiap scan**: `pipeline/scan.py` memanggil `ingest_candles(since = now − 120 hari, limit=500)`
   SETIAP scan, untuk 1H DAN 4H. Artinya tiap jam kita minta ~500 bar yang 99% sudah ada di DB.
   Boros credit TwelveData (free tier: 800 credit/hari, 8 req/menit), boros latency, dan menabrak
   rate limit saat instrumen bertambah.
2. **4H ikut di-refetch tiap jam** padahal bar 4H hanya berubah tiap 4 jam.
3. **Risiko look-ahead bar belum close**: provider (ccxt & TwelveData) menyertakan bar yang SEDANG
   berjalan. Scan jalan di candle_close+30s → bar baru (umur 30 detik) ikut ter-upsert ke DB dan bisa
   terbaca `latest_n(500)` sebagai "bar terakhir". Indikator/strategi mengira itu bar closed →
   sinyal bias. `timeutil.last_closed_candle_open()` SUDAH ADA tapi tidak dipakai untuk memfilter.

### Desain target
```
[SEKALI]  rtrade backfill --symbol XAUUSD --tf 1h --years 3..5
          → loop paginasi (TwelveData outputsize≤5000/call; ccxt ≤1000/call)
          → upsert idempotent (sudah ada) → resume-able kalau putus
          → kedalaman 3-5 tahun (syarat walk-forward §8.11.3 butuh ≥3 tahun)

[TIAP SCAN — incremental]
          watermark = MAX(ts) dari DB per (instrument, tf)
          since     = watermark − 2×tf      ← overlap 2 bar utk heal revisi provider
          limit     = 5-10 bar saja
          DROP bar dgn ts > last_closed_candle_open(tf, now)   ← buang bar forming (fix #3)
          → upsert → baca DB → indikator

[HARIAN]  gap-healing job: scan DB pakai detect_gaps() yang sudah ada
          → fetch HANYA rentang yang bolong (bukan refetch semua)

[JADWAL]  1H di-ingest tiap jam; 4H hanya di menit setelah 00/04/08/12/16/20 UTC;
          D1 sekali sehari. Quote live (GR-06) tetap per scan (1 credit, murah).
```

### Penghematan & efek
- Call provider per scan: dari ~500 bar × 2 TF → **5-10 bar × 1 TF** (4H hanya saat due).
- TwelveData: dari ~puluhan-ratusan credit besar/hari → hitungan kecil konstan; headroom besar
  untuk menambah instrumen & TF M15 (Fase C) tanpa upgrade plan berbayar.
- Scan lebih cepat (payload kecil) → sinyal terbit lebih dekat ke candle close = presisi entry naik.
- Bug look-ahead bar forming tertutup permanen (filter di ingestion DAN di query read).

### Sinergi dengan data 1m (inovasi I1) — supaya tidak meledak
- **Crypto**: backfill 1m penuh via ccxt (gratis, paginated) + compression policy TimescaleDB
  untuk chunk >90 hari (hypertable sudah ada sejak day one — tinggal aktifkan kompresi).
- **Forex/metals (TwelveData, credit terbatas)**: JANGAN ingest 1m terus-menerus. Fetch 1m
  **on-demand** hanya untuk rentang bar yang ambigu (SL dan TP tersentuh di bar yang sama) saat
  papertrack butuh memutuskan urutan kejadian. Per trade ambigu cuma 1-2 call kecil → resolusi
  eksak dengan biaya hampir nol.

### Posisi di roadmap
Masuk **Fase A sebagai A7** (bersama bug fix A6) — ini prasyarat semua fase lain:
backfill dalam = bahan bakar walk-forward (E1), incremental = bahan bakar scalping M15 (C),
on-demand 1m = bahan bakar replay jujur (I1).

---

## 10. Audit Temuan Gelombang 2 (file yang belum tersentuh review awal)

### 🔴 KRITIS — keselamatan & korektnes produksi

1. **GR-07 news blackout kemungkinan besar MATI total.**
   `finnhub_calendar.py:163` menyimpan `currency = row.get("country", ...)` — Finnhub mengembalikan
   kode NEGARA (`"US"`, `"GB"`, `"EU"`), sedangkan `instruments.yaml` mendeklarasikan MATA UANG
   (`USD`, `GBP`, `EUR`). `check_news_blackout` membandingkan string mentah → `"US" != "USD"` →
   **tidak pernah match → blackout tidak pernah aktif**. Bot bisa kirim sinyal 5 menit sebelum NFP.
   Fix: mapping country→currency saat ingest + verifikasi 1 fetch live.
2. **GR-07 fail-OPEN, bukan fail-CLOSED seperti yang didokumentasikan.**
   Docstring provider menjanjikan "calendar gagal → blokir sinyal forex/metals (fail-closed)".
   Realitas: kalender gagal sync → tabel events kosong → `check_news_blackout` return False →
   sinyal jalan terus TANPA proteksi berita, tanpa peringatan. Fix: jika `fetched_at` terakhir
   > X jam, perlakukan forex/metals sebagai blackout (fail-closed beneran) + alert.
3. **GR-12 menghitung sinyal REJECTED → bot jadi "pelit sinyal".**
   `count_since()` (repositories.py:218) tidak memfilter status. 3 kandidat yang GAGAL gate di hari
   yang sama = kuota harian habis = sinyal bagus berikutnya DIBLOKIR GR-12. Ini salah satu akar
   "terlalu pelit". Fix: hitung hanya status PUBLISHED.
4. **Cost tracking LLM menghasilkan angka sampah.**
   `client.py:223` memanggil `litellm.completion_cost(prompt=str(prompt_tokens), ...)` — argumen itu
   seharusnya TEKS, bukan jumlah token. Yang dihitung = token dari string "857" (≈3 token) → biaya
   tercatat ~nol → budget guard tidak melindungi. Laten sampai A1; wajib fix saat wiring LLM.

### 🟠 TINGGI — fitur "ada" tapi tidak pernah jalan (kepercayaan palsu)

5. **Scheduler hardcode 3 instrumen** (`scheduler/main.py:_SCAN_SCHEDULES`): GBPUSD, USDJPY,
   ETHUSDT ada di instruments.yaml tapi TIDAK PERNAH discan. Fix: generate jadwal dari config.
6. **Audit trail tidak pernah ditulis**: `AuditRepo` zero pemakaian; tabel `signal_audits` kosong
   selamanya. Janji "audit setiap keputusan ≥12 bulan" (PLAN §14.3) belum ditepati.
7. **GR-13 tidak benar-benar menonaktifkan strategi**: tabel `strategy_state` tidak pernah
   dibaca/ditulis. Expectancy negatif hanya menolak sinyal hari itu, tidak men-disable strategi.
8. **Telegram `/enable_strategy` BOHONG**: menjawab "✅ Strategi diaktifkan kembali" tanpa melakukan
   apa pun. Semua command bot (status/signals/calibration) masih stub statis; polling tidak pernah
   dijalankan oleh entrypoint mana pun; `/mute` tidak efektif (instance TelegramDelivery baru dibuat
   per scan, state mute hilang).
9. **AlertManager tidak pernah dipakai**: provider-down / scan-failed-3x / budget-80% — semua
   alert yang dijanjikan P4-T4 tidak aktif. Scan gagal = hanya baris log yang tak ada yang baca.
10. **Sinyal bisa PUBLISHED tapi tidak pernah terkirim**: `send_signal` menelan semua exception
    (log saja), commit DB terjadi sebelum kirim → kegagalan Telegram = sinyal "terbit" diam-diam.
    Fix: retry + tandai delivery status + alert.
11. **S2 news hard-block 12h tidak diimplementasikan**: config `news.hard_block_hours: 12` tidak
    pernah dibaca kode mana pun. Docstring S2 menjanjikannya.
12. **TwelveData D1 akan gagal parse**: format parse `"%Y-%m-%d %H:%M:%S"` sedangkan interval
    `1day` mengembalikan `"YYYY-MM-DD"` → semua candle harian di-skip sebagai "invalid row".
    Laten (D1 belum dipakai), meledak saat context_timeframe 1d diaktifkan.

### 🟡 SEDANG — presisi & efisiensi

13. **Burst rate-limit di batas jam**: semua scan dijadwalkan detik ke-30 bersamaan; bucket
    TwelveData 7/menit; di boundary 4 jam panggilan bisa melebihi bucket → sebagian scan abort.
    Fix: stagger detik per instrumen (30s, 45s, 60s…) + jadwal ingest pintar (§9).
14. **Analyst/Critic hardcode `gemini/gemini-3.1-flash-lite`** sebagai default, mengabaikan alias
    `trading-analyst/critic` dari settings — saat wiring A1, ambil model dari config.
15. **Backtester "skip exit di fill bar" itu OPTIMIS untuk SL** (komentar bilang conservative):
    SL yang kena di bar fill ditunda ke bar berikut → hasil bisa overstated. Resolusi jujur = I1.
16. **GR-13 pool lintas instrumen**: `recent_outcomes(strategy)` tidak filter instrumen — satu
    instrumen buruk bisa mematikan strategi di semua instrumen (atau sebaliknya tersamarkan).
17. **Confluence macro hampir selalu penuh**: `has_high_impact_event` diisi flag blackout ±30 menit,
    bukan "ada event high-impact dalam 12–24 jam ke depan" → komponen macro tidak diskriminatif.
18. **`score_structure` berhenti di level terdekat** meski arah tidak cocok (break-on-first) →
    support valid yang sedikit lebih jauh tidak dihitung.
19. **Koneksi churn**: `run_scan` membangun engine DB + Redis + provider + AppConfig.load setiap
    scan. Fix: objek long-lived di scheduler process.

### ✅ Yang terkonfirmasi SEHAT
`latest_n` benar (ascending), upsert candle idempotent, Lua token-bucket atomic, level engine solid,
schema frozen + validator GR-02/03/04 di dua lapis, dedup unik (instrument,tf,strategy,bar_ts) benar.

### Penambahan ke Fase A
- **A8 — Perbaikan kritis hasil audit-2**: item 1–4 (news blackout mapping + fail-closed, GR-12
  filter PUBLISHED, cost tracking) — kerjakan SEBELUM yang lain, ini guardrail keselamatan.
- **A9 — Aktifkan rantai kepercayaan**: scheduler dari config (5), audit trail (6), strategy_state
  + GR-13 persist (7), AlertManager wiring + delivery status (8–10), S2 hard-block (11).
