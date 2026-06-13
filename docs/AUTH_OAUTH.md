# OAuth & Credential Pool — Setup Guide

## Ikhtisar

Robil Trade mendukung **multi-provider LLM** dengan credential pool otomatis:

| Provider | Auth Mode | Cara Setup |
|----------|-----------|------------|
| Google Vertex AI | Google ADC (OAuth user / Service Account) | `rtrade auth login --provider google` |
| OpenAI (API) | API Key | `OPENAI_API_KEY_1..3` di `.env` |
| OpenAI (Codex OAuth) | Device Code Flow (langganan ChatGPT) | `rtrade auth login --provider codex_oauth` |
| xAI (API) | API Key | `XAI_API_KEY_1..3` di `.env` |
| xAI (OAuth) | Device Code Flow (langganan SuperGrok) | `rtrade auth login --provider xai_oauth` |
| Anthropic | API Key | `ANTHROPIC_API_KEY_1..3` di `.env` |
| Gemini | API Key | `GEMINI_API_KEY_1..5` di `.env` |

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
python -m rtrade.cli.auth login --provider xai_oauth
```

Sama seperti Codex: Device Code Flow → login di browser → token tersimpan otomatis.

### 4. Google Vertex AI

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
- Kredensial masuk **cooldown 60 detik**
- Pipeline otomatis **pindah ke kredensial berikutnya**
- Log warning: `credential kena limit/auth — fallback ke berikutnya`

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

## Deploy ke VPS

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
