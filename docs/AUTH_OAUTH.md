# OAuth & Credential Pool — Setup Guide

## Setup Wizard (jalur yang disarankan)

Cara tercepat: pakai wizard interaktif yang memandu pilih banyak provider × model,
lalu per provider pilih API key atau alur OAuth yang benar, dan memetakan ke role
(analyst/critic/flagship):

```bash
python -m rtrade.cli.setup wizard     # pilih provider × model × role
python -m rtrade.cli.setup verify     # bangun credential pool + cek; exit 0 bila pool terisi
```

`setup_vps.sh` sudah menjalankan `wizard` + auto-backfill (`rtrade backfill --all`,
fail-soft) otomatis; kredensial LLM tidak lagi dikumpulkan manual di step 5.
Alur per-provider (API key vs OAuth) dan catatan VPS ada di tabel di bawah.

## Ikhtisar

Robil Trade mendukung **multi-provider LLM** dengan credential pool otomatis:

| Provider | Auth Mode | Cara Setup |
|----------|-----------|------------|
| Google Vertex AI | Google ADC (OAuth user / Service Account) | `rtrade auth login --provider google` |
| OpenAI (API) | API Key | `OPENAI_API_KEY_1..3` di `.env` |
| OpenAI (Codex OAuth) | Device Code Flow (langganan ChatGPT) | `rtrade auth login --provider codex_oauth` |
| OpenRouter | API Key (satu key → 300+ model) | `OPENROUTER_API_KEY_1..3` di `.env` |
| xAI (API) | API Key | `XAI_API_KEY_1..3` di `.env` |
| xAI (OAuth) | PKCE Authorization-Code loopback (langganan SuperGrok) | `rtrade auth login --provider xai_oauth` |
| Anthropic | API Key | `ANTHROPIC_API_KEY_1..3` di `.env` |
| Gemini | API Key | `GEMINI_API_KEY_1..5` di `.env` |

## Matriks Alur OAuth

Alur OAuth **berbeda per provider**. Pakai tabel ini saat login di VPS/headless:

| Provider | Alur | Catatan VPS |
|----------|------|-------------|
| OpenAI Codex | Device Code Flow | Jalan langsung (headless, tanpa tunnel) |
| xAI Grok | PKCE Authorization-Code, redirect loopback `http://127.0.0.1:56121/callback` | `--manual-paste` (tempel URL callback) **atau** `ssh -N -L 56121:127.0.0.1:56121 user@vps` |
| Google Vertex | PKCE Authorization-Code (paste-URL) | `--manual-paste` atau `ssh -L` |

**xAI Grok = PKCE, BUKAN device code.** Wajib set env `RTRADE_XAI_CLIENT_ID`
(tidak di-hardcode; minta/verifikasi sendiri). Endpoint authorize/token default ke
`accounts.x.ai` (bisa di-override via `RTRADE_XAI_AUTHORIZE_URL`/`RTRADE_XAI_TOKEN_URL`/
`RTRADE_XAI_REDIRECT_URI`). Di VPS headless:

```bash
# Opsi A — tempel manual (tanpa tunnel):
python -m rtrade.cli.auth login --provider xai_oauth --manual-paste
# buka URL authorize di browser LOKAL → setujui → tempel URL callback lengkap.

# Opsi B — SSH tunnel loopback dari laptop, lalu login normal di VPS:
ssh -N -L 56121:127.0.0.1:56121 user@vps
```

## Quick Start

### 1. API Keys (Paling Sederhana)

```bash
# .env
GEMINI_API_KEY_1=AIza...
GEMINI_API_KEY_2=AIza...     # Opsional: fallback saat key 1 kena limit
XAI_API_KEY_1=xai-...
```

### 2. Codex OAuth (Langganan ChatGPT)

Login interaktif via Device Code Flow (gaya Hermes Agent):

```bash
python -m rtrade.cli.auth login --provider codex_oauth
```

Bot akan menampilkan:
```
============================================================
  Buka : https://auth.openai.com/authorize/device?user_code=ABCD-1234
  Kode : ABCD-1234
============================================================
  Menunggu Anda login di browser...
```

Buka URL tersebut, login dengan akun ChatGPT yang berlangganan, dan approve.
Token tersimpan otomatis di `~/.rtrade/tokens/codex_oauth.json` (terenkripsi).

**Multi-akun:**
```bash
python -m rtrade.cli.auth login --provider codex_oauth --account utama
python -m rtrade.cli.auth login --provider codex_oauth --account cadangan
```

### 3. xAI OAuth (Langganan SuperGrok / X Premium+)

```bash
# Set dulu di .env: RTRADE_XAI_CLIENT_ID=...
python -m rtrade.cli.auth login --provider xai_oauth
```

Berbeda dari Codex: xAI memakai **PKCE Authorization-Code** dengan redirect loopback
`http://127.0.0.1:56121/callback`, BUKAN device code. Di VPS headless gunakan
`--manual-paste` atau `ssh -N -L 56121:127.0.0.1:56121 user@vps` (lihat Matriks Alur
OAuth di atas). Token tersimpan otomatis, auto-refresh, masuk credential pool.

### 4. OpenRouter (satu key → 300+ model)

```bash
# .env
OPENROUTER_API_KEY_1=sk-or-...    # ambil di openrouter.ai/keys
```

### 5. Google Vertex AI

```bash
# Butuh GOOGLE_OAUTH_CLIENT_SECRETS env var yang menunjuk ke file client secrets
python -m rtrade.cli.auth login --provider google
```

## Credential Pool

Pool dibangun **otomatis** dari semua kredensial yang tersedia:

1. **API keys** (Gemini → Anthropic → OpenAI → xAI), urut slot
2. **OAuth CLI accounts** (codex_oauth, xai_oauth, generic_gateway)
3. **Vertex ADC accounts** (multi-akun Google)

Saat satu kredensial kena rate limit (429) atau auth error:
- Kredensial masuk **cooldown** (durasi tergantung tier, lihat di bawah)
- Pipeline otomatis **pindah ke kredensial berikutnya**
- Log warning: `credential kena limit/auth — fallback ke berikutnya`

### Adaptive cooldown / fallback limit 5 jam

Durasi cooldown menyesuaikan jenis error supaya pool merotasi dengan tepat:

| Tier | Pemicu | Default | Key `llm.pool` |
|------|--------|---------|----------------|
| Transient | 429 sesaat | 60 dtk | `cooldown_seconds` |
| Auth | key/token invalid | 300 dtk | `auth_cooldown_seconds` |
| Subscription | limit langganan/usage-window (~5 jam, mis. ChatGPT/SuperGrok) | 18000 dtk | `subscription_cooldown_seconds` |

Tier subscription memarkir kredensial selama window reset (~5 jam) sehingga pool
langsung berputar ke akun berikutnya. Konfigurasi di `config/settings.yaml` blok
`llm.pool`; tiap nilai harus di rentang (0, 21600] detik (6 jam):

```yaml
llm:
  pool:
    cooldown_seconds: 60
    auth_cooldown_seconds: 300
    subscription_cooldown_seconds: 18000
```

## Ops chat (read-only) di Telegram

Perintah status read-only (tidak bisa mengubah apa pun):

- `/pool` — status credential pool
- `/cost` — biaya LLM harian
- `/ask <pertanyaan>` — LLM menjawab dari snapshot status read-only (tak punya akses tulis/eksekusi)

## CLI Reference

```bash
# Login
python -m rtrade.cli.auth login --provider <id> [--account <label>] [--flow device_code]

# Status
python -m rtrade.cli.auth status [--provider <id>]

# List akun
python -m rtrade.cli.auth accounts --provider <id>

# List provider
python -m rtrade.cli.auth providers

# Diagnosa
python -m rtrade.cli.auth doctor --provider <id>

# Logout
python -m rtrade.cli.auth logout --provider <id> [--account <label>]

# Pool status
python -m rtrade.cli.auth pool

# Set model route
python -m rtrade.cli.auth use --role analyst --provider codex_oauth --model openai/gpt-4.1
```

## Deploy OAuth di VPS (penting)

OAuth (Vertex/Azure/gateway/dll.) butuh penyimpanan token persisten + terenkripsi:

1. **`RTRADE_TOKEN_KEY` WAJIB di prod.** Digenerate otomatis oleh `setup_vps.sh`. Tanpa ini,
   `auth login` gagal ("RTRADE_TOKEN_KEY wajib di prod").
2. **Token disimpan di volume persisten** `/app/data/auth/{tokens,adc}` (di-set via
   `RTRADE_TOKEN_DIR`/`RTRADE_ADC_DIR` di `docker-compose.prod.yml`). Container prod `read_only`,
   jadi token TIDAK boleh ke `~/.rtrade` (root FS read-only & non-persisten).
3. **Routing:** `auth login --provider <id> --account <acc>` (simpan token) lalu
   `auth use --role <role> --provider <id> --model <m> --account <acc>` (kaitkan route + buat
   `auth_profiles`). Cek hasil: `auth pool`.
4. Tambah kredensial baru → **restart** container app (pool dibangun sekali per proses).

Token OAuth bersifat **per-mesin**. Saat pindah ke VPS baru:

1. SSH ke VPS
2. Jalankan `python -m rtrade.cli.auth login --provider codex_oauth`
3. Device Code muncul → buka URL di browser laptop → approve
4. Token tersimpan di VPS, auto-refresh

**Penting:** Set `RTRADE_TOKEN_KEY` di `.env` untuk enkripsi token di prod:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Security

- Token **dienkripsi** dengan Fernet (env `RTRADE_TOKEN_KEY`)
- Di prod, token plaintext **ditolak** (fail-closed)
- File token: chmod 0600 (Linux)
- Rotasi key: `python -c "from rtrade.llm.auth.token_store import rotate_key; rotate_key('old', 'new')"`
- Consumer token Anthropic (`sk-ant-oat-*`) **diblokir** di semua field API key
