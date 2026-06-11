# Runbook: Rotasi API Key (90 Hari)

## Jadwal

Rotasi wajib setiap **90 hari** untuk semua API key. Set reminder di kalender.

| Key | Provider | Rotasi Terakhir | Rotasi Berikutnya |
|-----|----------|----------------|-------------------|
| GEMINI_API_KEY_1 | Google AI Studio | — | — |
| GEMINI_API_KEY_2 | Google AI Studio | — | — |
| TWELVEDATA_API_KEY | TwelveData | — | — |
| FINNHUB_API_KEY | Finnhub | — | — |
| LITELLM_MASTER_KEY | Self-generated | — | — |
| TELEGRAM_BOT_TOKEN | BotFather | — | — |
| API_AUTH_TOKEN | Self-generated | — | — |

## Prosedur Rotasi

### 1. Generate Key Baru

#### Gemini API Key
1. Buka https://aistudio.google.com/apikey
2. Create new API key
3. Catat key baru (JANGAN hapus key lama dulu)

#### TwelveData
1. Buka https://twelvedata.com/account/api-keys
2. Generate new key

#### Finnhub
1. Buka https://finnhub.io/dashboard
2. Regenerate API key

#### Self-generated Keys
```bash
# LITELLM_MASTER_KEY
openssl rand -hex 32

# API_AUTH_TOKEN
openssl rand -hex 32
```

### 2. Update di VPS

```bash
ssh robil-vps
cd /opt/robil-trade

# Edit .env
nano .env
# Update key yang dirotasi

# Restart services yang terpengaruh
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart litellm app api
```

### 3. Verifikasi

```bash
# Cek LiteLLM masih healthy
curl -s http://localhost:4000/health

# Cek API masih accessible
curl -H "Authorization: Bearer <NEW_TOKEN>" http://localhost:8000/health

# Cek Telegram bot
# Kirim /health di chat
```

### 4. Revoke Key Lama

Setelah verifikasi berhasil (tunggu 1 jam operasi normal):

1. Revoke/delete key lama di dashboard provider
2. Update tabel di atas dengan tanggal rotasi

## Darurat: Key Compromised

Jika key bocor:

1. **Segera revoke** key yang bocor di dashboard provider
2. Generate key baru
3. Update .env di VPS
4. Restart services
5. Audit log untuk aktivitas mencurigakan
6. Scan git history: `git log -p | grep -i "key\|token\|secret"`
7. Dokumentasikan insiden

## Catatan

- ⚠️ HANYA gunakan API key resmi (PLAN §14.2)
- DILARANG menggunakan OAuth token langganan konsumen
- `.env` harus permission 600: `chmod 600 .env`
- JANGAN pernah commit `.env` ke git
