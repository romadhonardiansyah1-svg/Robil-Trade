# Desain — Auto-Setup Penuh + Multi-Akun LLM (OAuth & API-key) dengan Fallback Limit 5 Jam

- Tanggal: 2026-06-21
- Status: DRAFT (menunggu persetujuan sebelum implementasi)
- Pemilik: Robil Trade (penggunaan pribadi / single-user)
- Terkait: `docs/AUTH_OAUTH.md`, `scripts/setup_vps.sh`, `src/rtrade/llm/auth/*`, `src/rtrade/llm/pool_builder.py`, `src/rtrade/llm/key_manager.py`

---

## 1. Konteks & Masalah

`setup_vps.sh` saat ini sudah "Hermes-style" (install Docker, UFW, clone, generate secret,
build, migrate, logrotate, verify), TAPI:

1. **Belum benar-benar otomatis.** Backfill data historis masih langkah manual ("NEXT STEPS:
   `./scripts/backfill_all.sh`"). Validasi/backtest juga manual.
2. **OAuth ditunda.** Step 5 hanya *menandai* provider OAuth (Vertex/Azure/gateway) lalu
   mencetak instruksi `rtrade auth login ...` untuk dijalankan **setelah** install. Untuk
   provider langganan ala Hermes (`codex_oauth`, `xai_oauth`) yang justru paling diminta user,
   login device-code belum dipandu di dalam wizard.
3. **Pemilihan model di awal belum terpusat.** User ingin: "di awal hanya memasukkan model AI
   yang dipakai" — satu wizard tunggal di awal yang menentukan provider + model + cara auth
   (OAuth vs API key), lalu sisanya jalan sendiri.
4. **Fallback "limit 5 jam" belum tepat.** `CredentialPool`/`KeyManager` memberi cooldown
   **tetap 60 detik** untuk semua kegagalan. Untuk langganan (Codex/SuperGrok) yang limitnya
   adalah jendela rolling ~5 jam, cooldown 60 detik membuat kredensial yang sama dicoba ulang
   terus dan gagal lagi — bukan rotasi yang benar ke akun berikutnya sampai jendela reset.

Logika bisnis kredensial saat ini sebagian tertanam di **bash** (step 5 `collect_credentials`)
yang tidak bisa diuji (`pytest`/`mypy`), padahal konvensi proyek: mypy strict + TDD.

## 2. Tujuan & Non-Tujuan

### Tujuan
- T1. **Setup satu-perintah** di VPS Ubuntu: user hanya menjawab wizard model AI di awal;
  install, secrets, build, migrate, **backfill**, dan verifikasi berjalan otomatis.
- T2. **Wizard model gaya Hermes (`hermes model`)**: tampilkan **daftar banyak provider**, dan
  user bisa menambah **banyak (provider × model)** sekaligus — bukan sekadar "isi Gemini API key".
  Tiap entri memilih jalur auth yang **tepat per provider**:
  (a) **API key** (banyak kunci/akun); (b) **OAuth dengan flow yang benar per provider**
  (device-code untuk Codex; PKCE-loopback/paste untuk Google & xAI; dst).
- T2b. **Maksimalkan kesetiaan OAuth per provider**: Codex = device-code (kode mesin); Google &
  xAI = PKCE browser/loopback dengan fallback **manual-paste** (VPS-friendly). Perbaiki xAI yang
  saat ini SALAH dikonfigurasi sebagai device_code.
- T2c. **Jangkauan model luas seperti Hermes**: sediakan jalur **OpenAI-compatible / OpenRouter**
  (litellm `api_base` + key) sehingga ratusan model lintas provider bisa dipakai lewat satu jalur
  generik — mendekati pengalaman "300+ model" Hermes/Nous Portal.
- T3. **Fallback adaptif**: saat satu akun kena limit, rotasi ke akun berikutnya; akun yang
  kena **limit langganan (jendela ~5 jam)** masuk cooldown panjang (sampai perkiraan reset),
  bukan 60 detik, sehingga tidak dicoba ulang sia-sia.
- T4. Logika kredensial/model/backfill **dapat diuji** (pindah dari bash ke Python CLI).
- T5. Tidak ada rahasia yang ter-log/echo; token OAuth tetap terenkripsi (Fernet) & fail-closed.

### Non-Tujuan
- N1. Tidak menambah eksekusi order/trading (tetap signal-only).
- N2. Tidak membuat UI web baru; wizard tetap CLI/terminal.
- N3. Tidak men-deploy ke layanan pihak ketiga selain VPS milik user.
- N4. Tidak mengubah katalog model atau menambah provider baru di luar yang sudah ada di
  `oauth_providers.example.yaml`.

## 3. Keadaan Saat Ini (yang sudah berfungsi — TIDAK perlu dibangun ulang)

- **OAuth device-code "Hermes-style"** sudah ada: `OAuth2Provider.device_login()` (Codex 2-langkah
  + RFC 8628), `codex_oauth`/`xai_oauth` di `oauth_providers.example.yaml`, CLI
  `python -m rtrade.cli.auth login --provider <id> --account <label>`.
- **Multi-akun** sudah didukung: token store per `provider__account`, `list_accounts`,
  `auth accounts`, `auth status`, `auth pool`.
- **Multi API key** sudah didukung: `GEMINI_API_KEY_1..5`, `ANTHROPIC_API_KEY_1..3`,
  `OPENAI_API_KEY_1..3`, `XAI_API_KEY_1..3` (lihat `Secrets.keys_for`).
- **Credential pool + rotasi**: `build_scan_pool` menggabungkan SEMUA kredensial (API key +
  OAuth CLI + Vertex ADC) jadi satu pool; `CredentialPool.acquire/report_failure`,
  `classify_llm_error` (rate_limit/auth/other), cooldown via `KeyManager`.
- **Enkripsi token**: Fernet via `RTRADE_TOKEN_KEY` (di-generate `setup_vps.sh`), fail-closed di prod.

## 4. Gap yang harus ditutup

| # | Gap | Lokasi |
|---|-----|--------|
| G1 | Backfill tidak otomatis dalam setup | `setup_vps.sh` step 9 (manual) |
| G2 | OAuth login tidak dipandu di dalam wizard | `setup_vps.sh` step 5 |
| G3 | Tidak ada "wizard model" terpusat di awal (gaya `hermes model`) | belum ada |
| G4 | Cooldown tetap 60s untuk semua kegagalan (limit 5 jam salah ditangani) | `key_manager.py`, `pool.py` |
| G5 | Logika kredensial di bash, tak teruji | `setup_vps.sh` |
| G6 | `classify_llm_error` belum membedakan limit langganan vs 429 trans | `pool.py` |
| **G7** | **xAI OAuth SALAH dikonfigurasi sebagai `device_code`** — xAI sebenarnya **PKCE loopback** (accounts.x.ai, port 56121). Login device-code ke xAI akan gagal. | `config/oauth_providers.example.yaml`, `cli/auth.py` |
| **G8** | **PKCE paste-URL belum di-wire ke dispatch login** — `generate_pkce_pair`/`build_authorize_url`/`exchange_pasted_redirect` ada tapi hanya Google (via google-auth-oauthlib) yang memakai PKCE; provider PKCE lain (xAI/Qwen) jatuh ke `device_login()`. | `cli/auth.py` `_cmd_login` |
| **G9** | **Jangkauan model sempit** — hanya flavor {gemini, anthropic, openai, xai, vertex, azure}. Tidak ada jalur OpenAI-compatible/OpenRouter untuk "banyak provider & model" gaya Hermes. | `Secrets`, `pool.py`, `model_router.py` |
| **G10** | **VPS/headless OAuth belum dipandu** — provider PKCE-loopback butuh `ssh -L` tunnel atau `--manual-paste`; belum dideteksi/dipandu otomatis. | `cli/auth.py`, `setup_vps.sh`, docs |

## 5. Pendekatan (opsi)

### Opsi A — Tetap full-bash
Tambah backfill + OAuth prompt langsung di `setup_vps.sh`.
- (+) Tidak ada file baru. (−) Logika makin tebal di bash; tak teruji; sulit di-mypy/pytest;
  duplikasi dengan `rtrade.cli.auth`. **Ditolak** (melanggar konvensi TDD/strict).

### Opsi B — Full-Python wizard
Ganti hampir seluruh `setup_vps.sh` dengan `python -m rtrade.cli.setup`.
- (+) Semua teruji. (−) Provisioning root (apt/docker/ufw/useradd) memang ranah shell;
  memaksakan ke Python menambah kompleksitas (perlu Python lebih dulu, jalan sebagai root).

### Opsi C — Hybrid (REKOMENDASI)
- **Bash** (`setup_vps.sh`) tetap menangani provisioning sistem (cek, apt, docker, ufw, user,
  clone, generate secret dasar) — tapi langkah "kredensial + model + OAuth + backfill"
  **didelegasikan** ke CLI Python baru yang berjalan **di dalam container app**.
- **Python** (`rtrade.cli.setup`) berisi: wizard model, login OAuth device-code, kumpul API key
  multi-akun, tulis `.env`/`settings.yaml` route, lalu orkestrasi backfill + verifikasi.
- (+) Provisioning di tempat yang benar (shell), logika bisnis teruji (Python, mypy strict, pytest).
  (+) Login OAuth memakai jalur `rtrade.cli.auth` yang sudah ada → "plek ketiplek". (−) Dua bahasa,
  tapi batas tanggung jawab jelas.

**Keputusan: Opsi C.**

## 6. Desain Rinci

### 6.1 CLI baru `rtrade.cli.setup` (wizard gaya `hermes model`)
Tujuan UX: meniru `hermes model` — satu menu provider yang kaya, user menambah **banyak entri
(provider × model)**, tiap entri memilih jalur auth yang benar. Semua entri otomatis menjadi
anggota **credential pool** + **rantai fallback** (urut sesuai penambahan).

Subcommand:
- `rtrade setup wizard` — alur interaktif (dipanggil otomatis oleh `setup_vps.sh`, atau manual).
  Loop utama (bisa tambah sebanyak yang diinginkan):
  1. **Menu provider** (lihat katalog 6.1b). User pilih satu.
  2. **Pilih model** dari katalog provider tersebut (atau ketik manual / auto-detect via `/models`
     untuk endpoint OpenAI-compatible).
  3. **Pilih jalur auth** sesuai dukungan provider:
     - **API key** → kumpul 1..N kunci (multi-akun; tolak `sk-ant-oat*`).
     - **OAuth** → jalankan **flow yang benar** untuk provider itu (lihat matriks 6.2).
  4. **Entri tersimpan**: tulis `auth_profiles` + (opsional) slot `.env` + masuk daftar entri.
  5. "Tambah provider/model lain? [y/N]" → ulangi.
  - **Setelah selesai**: petakan entri ke **peran** (`analyst`, `critic`, `flagship`) — default:
    entri pertama = analyst, dst; atau user pilih. Sisanya tetap di pool sebagai fallback.
    Tulis `model_routes` lewat pustaka bersama (ekstrak inti `_cmd_use`).
- `rtrade setup verify` — `build_scan_pool` + ringkas status (`auth pool`); exit≠0 bila kosong.
- Mode non-interaktif (`--from-file plan.yaml`) untuk uji & re-run idempoten.

### 6.1b Katalog provider (jangkauan luas seperti Hermes)
Wizard menampilkan provider berikut. Yang bertanda ⊕ adalah penambahan baru untuk jangkauan luas:

| Provider (wizard) | flavor litellm | Jalur auth | Catatan |
|-------------------|----------------|-----------|---------|
| Google Gemini (API key) | `gemini` | API key (`GEMINI_API_KEY_1..5`) | termudah |
| Anthropic (API key) | `anthropic` | API key (`ANTHROPIC_API_KEY_1..3`) | |
| OpenAI (API key) | `openai` | API key (`OPENAI_API_KEY_1..3`) | |
| xAI Grok (API key) | `xai` | API key (`XAI_API_KEY_1..3`) | |
| OpenAI Codex (langganan) | `openai` | **OAuth device-code** | "plek ketiplek" Hermes |
| xAI Grok (SuperGrok/X Premium+) | `xai` | **OAuth PKCE-loopback + manual-paste** | FIX dari device_code |
| Google Vertex AI | `vertex_ai` | OAuth Google (ADC, PKCE loopback/paste) | multi-akun |
| Azure OpenAI | `azure` | Azure AD client-credentials | enterprise |
| ⊕ OpenRouter | `openrouter` (litellm) | API key (`OPENROUTER_API_KEY`) | **300+ model 1 key** (mirip Nous Portal) |
| ⊕ Custom OpenAI-compatible | `openai` + `api_base` | API key + base_url | Groq/Together/DeepSeek/vLLM/Ollama, dll |

Penambahan ⊕ membutuhkan: (a) field Secrets/config baru untuk `OPENROUTER_API_KEY` dan
`custom_providers` (base_url + key_env), (b) `model_flavor`/pemanggilan litellm yang menghormati
`api_base`, (c) entri pool ber-flavor generik. Ini cara berbiaya-rendah/leverage-tinggi untuk
"banyak provider & model" karena OpenRouter sendiri sudah memproksi mayoritas lab model.

### 6.2 Matriks OAuth per provider (G7/G8/G10 — "maksimalkan")
Berdasarkan studi Hermes-agent, flow login berbeda per provider dan HARUS dihormati:

| Provider | Flow benar | Headless/VPS | Status repo |
|----------|-----------|--------------|-------------|
| **Codex (ChatGPT)** | **device-code** (tampilkan URL + kode mesin, poll token) | jalan langsung (tanpa tunnel) | ✅ ada (`device_login`) |
| **GitHub Copilot** *(opsional)* | device-code | jalan langsung | belum (opsional) |
| **Google Gemini / Vertex** | **PKCE Authorization-Code** (browser, callback `127.0.0.1`) | **paste-URL** (sudah ada) atau `ssh -L` | ✅ ada (`_google_login` `paste_url`) |
| **xAI Grok (SuperGrok)** | **PKCE loopback** (accounts.x.ai, callback `127.0.0.1:56121`) | **manual-paste** atau `ssh -L 56121` | ❌ **SALAH** (device_code) → FIX |
| **Anthropic (Claude Max)** *(opsional)* | **paste-the-code** | paste | belum (opsional) |
| **Generic gateway** | device-code / client-credentials | jalan langsung | ✅ ada |

**Mekanisme yang ditambah/diperbaiki:**
1. **`login_flow` dihormati di dispatch.** `_cmd_login`/wizard membaca `login_flow` profil
   (`device_code` | `pkce_loopback` | `paste_url` | `paste_code`) dan memanggil jalur yang sesuai —
   bukan selalu `device_login()`.
2. **PKCE loopback + manual-paste** untuk xAI/Qwen: pakai `generate_pkce_pair` +
   `build_authorize_url` (verifier/state/nonce sama untuk loopback & paste — bukan downgrade
   keamanan, persis Hermes), lalu `exchange_pasted_redirect`. Listener loopback `127.0.0.1:<port>`
   bila ada browser lokal; bila headless → cetak URL + minta tempel URL callback/kode.
3. **Deteksi headless/VPS** (`HERMES`-style: tak ada `$DISPLAY`/ada `$SSH_CONNECTION`/flag
   `--manual-paste`): default ke paste-mode + cetak petunjuk `ssh -N -L <port>:127.0.0.1:<port>`.
4. **Perbaiki manifest xAI**: ubah `xai_oauth` ke `login_flow: pkce_loopback`, isi authorize-url +
   token-url + redirect/port yang benar. **Konstanta spesifik xAI (client_id, authorize/token URL,
   port 56121, scopes) DIVERIFIKASI dari implementasi Hermes / dikonfirmasi user — tidak ditebak.**
   Sediakan via env (`RTRADE_XAI_*`) supaya bisa diisi tanpa mengubah kode bila xAI mengubah nilai.
5. Codex tetap memakai jalur device-code yang sudah terbukti — tidak diubah.

> Catatan ToS/risiko: jalur OAuth langganan (Codex/xAI/Google-CLI) memiliki risiko kebijakan
> provider (lihat peringatan Hermes). Wizard menampilkan peringatan singkat + minta konfirmasi
> eksplisit sebelum login OAuth langganan, dan menyarankan API key sebagai jalur paling aman.

### 6.3 Multi-akun & generasi `.env`
- API key ditulis ke `.env` sebagai `*_API_KEY_1..N` (slot sudah didukung `Secrets`).
- OAuth: tidak ada secret di `.env` (token di token store terenkripsi). Hanya `RTRADE_TOKEN_KEY`
  (sudah digenerate `setup_vps.sh`) yang wajib.
- Wizard mem-backup `.env` lama (`.env.bak.<ts>`) bila menulis ulang; permission tetap `600`.

### 6.4 Backfill otomatis (G1)
- `setup_vps.sh` step baru (setelah migrate, sebelum verify final): jalankan backfill di dalam
  container app untuk semua instrumen di `instruments.yaml` × timeframe-nya, memakai
  `scripts/backfill_all.sh` / `rtrade.cli.backfill` yang sudah ada.
- **Idempoten & fail-soft**: backfill gagal (mis. key data belum diisi) → WARNING + lanjut
  (bot tetap bisa start; user dapat ulang backfill nanti). Ringkasan menampilkan status per simbol.
- Disediakan flag `--skip-backfill` untuk re-run cepat.

### 6.5 Fallback "limit 5 jam" — cooldown adaptif (G4, G6)
Inti perubahan (di `key_manager.py` + `pool.py`):

1. **`KeyManager.report_rate_limit(provider, key, *, cooldown_seconds: int | None = None)`** —
   terima override durasi; default ke `self._cooldown_sec` bila `None`. Redis `setex` & memori
   fallback memakai durasi ini.
2. **`CredentialPool.report_failure(cred_id, *, kind, cooldown_seconds: int | None = None)`** —
   teruskan override.
3. **Klasifikasi diperluas** (`pool.py`): tambah deteksi *usage/subscription limit* (mis. pesan
   mengandung `usage limit`, `daily limit`, `quota exceeded`, `try again in`, atau header
   `Retry-After`/`x-ratelimit-reset`). Pemanggil pipeline memetakan:
   - **Subscription/usage limit** (Codex/xAI OAuth, jendela ~5 jam) → cooldown panjang
     `subscription_cooldown_seconds` (config, default `18000` = 5 jam). Bila ada `Retry-After`/
     reset-epoch → pakai itu (dibulatkan), maksimum cap 6 jam.
   - **429 transien biasa** → cooldown pendek (default 60s) seperti sekarang.
   - **auth error** → cooldown sedang (mis. 300s) + log "perlu login ulang".
4. **Konfigurasi** di `settings.yaml` blok `llm` (atau `llm.pool`):
   ```yaml
   llm:
     pool:
       cooldown_seconds: 60            # 429 transien
       auth_cooldown_seconds: 300      # error auth
       subscription_cooldown_seconds: 18000  # limit langganan ~5 jam
   ```
   Default aman bila blok tak ada (backward-compatible).
5. **Persisten lintas proses**: cooldown disimpan di Redis (sudah ada), jadi rotasi limit 5 jam
   bertahan walau proses restart. Memori fallback tetap untuk dev tanpa Redis.

Catatan: kita TIDAK menebak reset window provider secara presisi; kita memakai default
konservatif (5 jam) yang bisa ditimpa oleh header `Retry-After` bila tersedia. Ini memastikan
akun yang limit "diparkir" dan akun lain dipakai, sesuai permintaan user.

### 6.6 Integrasi `setup_vps.sh`
- Step 5 (`collect_credentials`) **dirampingkan**: tetap generate secret non-LLM (DB, AUTH,
  TOKEN_KEY), minta data-provider keys (TwelveData/Finnhub/OANDA) + Telegram + domain, tulis `.env`.
  Bagian **pemilihan model & LLM auth dipindah** ke wizard Python (dijalankan setelah app up).
- Urutan baru: cek → install → security → clone → **collect (non-LLM) & .env** → build → migrate →
  **`exec app rtrade setup wizard` (model + OAuth/API key)** → **backfill otomatis** → logrotate →
  verify (termasuk `auth pool`). Semua tanpa intervensi selain wizard model di awal sesi.

## 7. Jawaban: kredensial apa yang perlu dikirim untuk tes maksimal & deploy lancar

> Catatan keamanan: kirim lewat kanal aman; di kode/log semua dirujuk **berdasarkan nama key**,
> nilai tidak pernah dicetak. Untuk **uji lokal** Anda cukup mengisi `.env` di mesin Anda — Anda
> tidak harus mengirim nilai mentah ke saya; saya bisa menulis kode + tes dengan **kunci dummy**.

**Wajib agar bot jalan & sinyal keluar:**
- Data pasar (minimal satu): `OANDA_TOKEN_1` + `OANDA_ACCOUNT_1` (praktik/live) — utama FX/metals;
  dan/atau `TWELVEDATA_API_KEY`. `FINNHUB_API_KEY` opsional (kalender/berita).
- LLM (minimal satu jalur): salah satu dari
  - **API key**: `GEMINI_API_KEY_1` (paling mudah, gratis di aistudio) — atau `ANTHROPIC_API_KEY_1`
    / `OPENAI_API_KEY_1` / `XAI_API_KEY_1`; atau
  - **OAuth langganan** (tanpa mengirim apa pun ke saya): login sendiri di VPS via
    `rtrade auth login --provider codex_oauth` / `--provider xai_oauth`.
- Pengiriman sinyal: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`.
- Keamanan/integritas: `API_AUTH_TOKEN`, `MODEL_HMAC_KEY`, dan di prod `RTRADE_TOKEN_KEY`
  (ketiganya bisa di-generate otomatis; tidak perlu dari pihak luar).

**Untuk menguji fallback multi-akun limit 5 jam secara nyata:** ≥2 kredensial pada satu flavor,
mis. `GEMINI_API_KEY_1` + `GEMINI_API_KEY_2`, atau dua akun `codex_oauth` (`--account utama`,
`--account cadangan`). (Untuk unit test, saya pakai mock — tak perlu kredensial asli.)

**Yang TIDAK boleh dikirim / dilarang kode:** token konsumen `sk-ant-oat...` (ditolak oleh
`Secrets` & wizard). 

**Rekomendasi untuk "tes maksimal" tanpa risiko:** kirim hanya kredensial **tier gratis/practice**
(OANDA practice, Gemini free, TwelveData free, bot Telegram khusus). Simpan kredensial berbayar/
langganan hanya di VPS Anda via OAuth login langsung.

## 8. Keamanan
- Tidak meng-echo/log nilai rahasia (lanjutkan pola `_mask`, redaksi rekursif C9).
- Token OAuth tetap Fernet-encrypted, fail-closed di prod, file `0600`.
- Wizard menolak `sk-ant-oat*`.
- `.env` permission `600`, owner `rtrade`; backup `.env.bak.*` juga `600`.
- Cooldown panjang tidak menyimpan detail error berisi rahasia (hanya `cred_id` + `kind`).

## 9. Strategi Test (TDD)
- **Unit (pytest, mypy strict):**
  - `key_manager`: cooldown override (Redis & memori), expiry, exhausted.
  - `pool`: `report_failure(kind=..., cooldown_seconds=...)`; `classify_llm_error` membedakan
    subscription-limit vs 429 vs auth (tabel kasus pesan/exception).
  - `cli.setup`: pemilihan model menulis `model_routes`/`auth_profiles` benar; jalur API-key
    menulis slot env; jalur OAuth memanggil `device_login` (mock); tolak `sk-ant-oat*`;
    idempoten (re-run tak menduplikasi).
  - Mapping kind→cooldown dari `settings.llm.pool`.
- **Tidak menambah** kompleksitas bash-test; `setup_vps.sh` diverifikasi manual + `bash -n`
  (syntax) di CI bila memungkinkan.

## 10. Risiko & Mitigasi
- R1. Reset window provider tak diketahui presisi → default 5 jam konservatif + hormati
  `Retry-After`. (Akun bisa "diparkir" sedikit lebih lama dari perlu; aman.)
- R2. Backfill gagal saat key data belum lengkap → fail-soft + WARNING, bot tetap start.
- R3. OAuth device-code butuh interaksi browser → wizard berhenti menunggu (by design); ada
  timeout `expires_in` (sudah ada, C7).
- R4. Refactor `_cmd_use`/login ke pustaka bisa menyentuh `cli/auth.py` → jaga perilaku CLI lama
  dengan tes regresi.

## 11. Keputusan TERKUNCI (disetujui user 2026-06-21)
- **K1 = YA**: sertakan OpenRouter + custom OpenAI-compatible endpoint.
- **K2**: OAuth prioritas = Codex (siap) + xAI (FIX→PKCE) + Google-Gemini-CLI. Anthropic/Copilot opsional menyusul.
- **K3**: konstanta xAI PKCE env-driven (`RTRADE_XAI_*`) + default dari studi Hermes untuk diverifikasi.
- **K4 = YA**: bangun Tingkat 1 (ops chat READ-ONLY) dalam scope ini.
- **K5**: Tingkat 2 (diagnose + propose patch) = **spec terpisah nanti** (TIDAK dalam scope ini).
- **K6 = YA**: perbaikan bug via agen developer (Kiro/Hermes) + izin user; **TIDAK** auto-apply oleh bot live.

## 11b. Keputusan teknis default (diambil bila tak ada arahan)
- D1. Default `subscription_cooldown_seconds = 18000` (5 jam). 
- D2. Cap maksimum cooldown 6 jam (21600s) bila `Retry-After` lebih besar.
- D3. Wizard menangani peran `flagship` sebagai opsional (boleh "skip / sama dengan analyst").
- D4. Backfill default: semua instrumen × semua timeframe terdaftar; lookback ikut default
  `backfill_all.sh`.

---

### Lampiran A — Daftar perubahan file (perkiraan)
- BARU: `src/rtrade/cli/setup.py` (wizard gaya `hermes model` + verify).
- BARU: `src/rtrade/llm/auth/wizard.py` (pustaka inti dipakai cli/setup & cli/auth).
- BARU: `src/rtrade/llm/auth/pkce_login.py` (PKCE loopback + manual-paste; bungkus
  `generate_pkce_pair`/`build_authorize_url`/`exchange_pasted_redirect`) — atau perluas `oauth2.py`.
- UBAH: `src/rtrade/llm/key_manager.py` (cooldown override).
- UBAH: `src/rtrade/llm/auth/pool.py` (report_failure override + klasifikasi diperluas).
- UBAH: `src/rtrade/core/config.py` (blok `llm.pool` + `custom_providers` + `OPENROUTER_API_KEY`).
- UBAH: `src/rtrade/llm/pool_builder.py` (flavor `openrouter`/custom + `api_base`).
- UBAH: `src/rtrade/llm/model_router.py` (auth_profile bawa `base_url`/flavor generik).
- UBAH: `src/rtrade/cli/auth.py` (`_cmd_login` hormati `login_flow`; PKCE + manual-paste; `--manual-paste`/`--no-browser`).
- UBAH: `config/oauth_providers.example.yaml` (FIX `xai_oauth`→pkce_loopback; tambah login_flow eksplisit).
- UBAH: pemanggil pipeline yang menangani kegagalan LLM (peta kind→cooldown).
- UBAH: `scripts/setup_vps.sh` (delegasi wizard + backfill otomatis + petunjuk SSH/manual-paste).
- UBAH: `config/settings.yaml`, `docs/AUTH_OAUTH.md`, `.env.example`.
- TES: `tests/llm/test_key_manager*.py`, `tests/llm/test_pool*.py`, `tests/cli/test_setup*.py`,
  `tests/llm/auth/test_pkce_login*.py`, `tests/cli/test_auth_login_flows*.py`.

### Lampiran B — Referensi flow Hermes-agent (studi)
- `hermes model` = wizard provider+model interaktif; `hermes setup` mendelegasikan ke sana.
- Codex/Copilot/Nous Portal/MiniMax = **device-code** (tanpa tunnel di remote).
- Google Gemini (`google-gemini-cli`) = **PKCE** callback `127.0.0.1:8085`, paste-mode untuk headless.
- xAI Grok OAuth = **PKCE loopback** `127.0.0.1:56121`, `--no-browser`+`ssh -L` atau `--manual-paste`
  (PKCE verifier/state/nonce sama → paste bukan downgrade keamanan).
- Anthropic (Claude Max) = **paste-the-code**.
- `fallback_providers:` = rantai backup provider+model dicoba berurutan saat rate-limit/error/auth —
  konsep yang kita realisasikan via credential pool + cooldown adaptif.
- Sumber: docs Hermes `integrations/providers.md`, `developer-guide/adding-providers.md`,
  `guides/xai-grok-oauth.md`, `guides/oauth-over-ssh.md`. (Diparafrase untuk kepatuhan lisensi.)

---

## 12. Katalog provider extensible + klarifikasi "Kiro/Hermes"

- **Katalog provider data-driven.** Wizard membaca daftar provider dari manifest
  (`oauth_providers.yaml` + tabel first-class + `custom_providers`). Menambah provider baru =
  menambah entri data, BUKAN mengubah kode. Provider OpenAI-compatible apa pun (Groq, Together,
  DeepSeek, Mistral, Cerebras, Fireworks, vLLM/Ollama lokal, gateway internal, dll.) masuk lewat
  jalur **Custom OpenAI-compatible endpoint** (`base_url` + `key_env`).
- **OpenRouter** = jalur "banyak model satu kunci" (≈300+ model) — paling dekat dengan pengalaman
  Nous Portal; jadi entri first-class.
- **"Kiro" / "Hermes" bukan provider inferensi.** Keduanya adalah agen developer (mengedit repo).
  Mereka TIDAK masuk credential pool sinyal. Kebutuhan "model menguasai bot / memperbaiki bug"
  ditangani di Bagian 13 (agen developer), bukan sebagai provider pool. Jika ada endpoint
  ber-API OpenAI-compatible yang Anda sebut "Kiro", ia tetap bisa ditambah via jalur custom.

## 13. Ops Assistant (chat dengan bot) + Self-Healing — DESAIN BERTINGKAT (sensitif keamanan)

Permintaan: (a) bisa chat dengan bot soal kondisi terkini; (b) bot/model bisa memperbaiki bug/
error saat terjadi; (c) perubahan **atas izin Anda**; (d) atau pasang Hermes-agent untuk perbaikan.

Risiko: ini bot trading **signal-only** dengan **golden rules fail-closed**. Membiarkan LLM
mengedit kode yang sedang berjalan dapat **merusak jaminan keamanan** (mis. melonggarkan GR-05,
mematikan news blackout, dsb.). Maka desain dibagi **tiga tingkat**, dari aman → berisiko:

### Tingkat 1 — Ops Chat READ-ONLY (REKOMENDASI, risiko rendah)
"Tanya bot": status, health, sinyal terakhir, equity/posisi (manual), error/log terbaru, biaya LLM,
status credential pool, kalender berita. Implementasi:
- Perluas bot Telegram (sudah ada) + endpoint API read-only → konteks dikumpulkan
  (health, metrics, audit/log ringkas, `auth pool`) lalu diringkas oleh LLM dari pool yang ada.
- LLM hanya **membaca** snapshot yang sudah diredaksi (tanpa rahasia). TIDAK ada akses tulis.
- Perintah contoh: `/status`, `/why <signal_id>`, `/errors`, `/health`, `/pool`, `/cost`,
  lalu `/ask <pertanyaan bebas>` (jawaban dibatasi konteks read-only).
- Aman: tidak menyentuh kode/konfigurasi/eksekusi. Cocok dikerjakan dalam scope ini.

### Tingkat 2 — Diagnose + PROPOSE patch (gated, risiko sedang)
Saat terjadi error/exception, bot:
1. Kumpulkan traceback + konteks file terkait (read-only).
2. LLM mendiagnosis akar masalah & **menyusun usulan patch** (diff) — **TIDAK menerapkan**.
3. Usulan dikirim ke Anda (Telegram/PR) → Anda review.
4. Penerapan HANYA via jalur git biasa: branch baru → gate penuh (`ruff`+`mypy --strict`+`pytest`)
   → **persetujuan eksplisit Anda** → redeploy terkontrol. TIDAK pernah auto-apply ke prod live.
- Pemisahan lingkungan: diagnosis/patch dibuat di **worktree/branch dev**, bukan di proses bot live.

### Tingkat 3 — Auto-apply (TIDAK DIREKOMENDASIKAN untuk bot trading)
LLM menerapkan perbaikan sendiri ke kode berjalan. Ditolak secara default: melanggar prinsip
fail-closed & dapat mengubah golden rules tanpa jejak review. Bila kelak diinginkan, wajib: sandbox
terisolasi, allowlist file yang boleh disentuh (tidak termasuk `core/config.py`, `signals/`,
`risk/`, guardrails), gate hijau wajib, dan kill-switch. Di luar scope rencana ini.

### Opsi "pasang Hermes-agent untuk perbaikan"
Ini sebenarnya pola **paling bersih** untuk Tingkat 2: jalankan **Kiro (IDE ini) atau Hermes-agent**
sebagai **agen developer terpisah** pada repo (di mesin dev / worktree), dengan izin Anda per
perubahan, lalu commit→gate→deploy. Bot trading sendiri tetap fokus (maksimal Tingkat 1 ops-chat).
Rekomendasi: **bot = Tingkat 1 (ops chat read-only)**; **perbaikan bug = agen developer
(Kiro/Hermes) di luar proses bot, dengan persetujuan Anda + gate penuh.**

### Keputusan untuk Bagian 13
- **K4.** Implementasikan **Tingkat 1 (ops chat read-only)** dalam scope ini? (Rekomendasi: ya.)
- **K5.** Tingkat 2 (diagnose + propose patch, gated) — sekarang, atau nanti sebagai spec terpisah?
  (Rekomendasi: spec terpisah; ini fitur besar + sensitif.)
- **K6.** Setuju bahwa perbaikan bug ditangani agen developer (Kiro/Hermes) di luar proses bot,
  BUKAN auto-apply oleh bot live? (Rekomendasi: ya — paling aman.)
