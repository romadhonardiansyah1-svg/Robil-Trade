# RUNBOOK: Aktivasi Infrastruktur Robil Trade (V4)

> **PENTING**: File ini adalah instruksi untuk USER. Jangan commit `.env` atau secret apa pun ke Git.

---

## 1. Environment Variables (`.env`)

Buat file `.env` di root project (`c:\Robil Trade\robil-trade\.env`):

```dotenv
# Database
DATABASE_URL=postgresql+asyncpg://rtrade:YOUR_PASSWORD@localhost:5432/rtrade

# Redis
REDIS_URL=redis://localhost:6379/0

# Data Providers
TWELVEDATA_API_KEY=your_twelvedata_key_here
FINNHUB_API_KEY=your_finnhub_key_here

# LLM
GEMINI_API_KEY_1=your_gemini_api_key_here
LITELLM_BASE_URL=http://localhost:4000

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# API Auth
API_AUTH_TOKEN=your_random_auth_token_here
```

---

## 2. Stack Up

```powershell
# Start database + Redis
docker compose up -d db redis

# Run migrations
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run alembic upgrade head
```

Verifikasi:
```powershell
docker compose ps  # db dan redis harus STATUS "running"
```

---

## 3. Backfill Data

### 3a. Crypto (gratis, cepat — jalankan dulu)

```powershell
# BTCUSDT 1h, 3 tahun
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol BTCUSDT --tf 1h --years 3

# BTCUSDT 4h
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol BTCUSDT --tf 4h --years 3

# ETHUSDT 1h + 4h
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol ETHUSDT --tf 1h --years 3
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol ETHUSDT --tf 4h --years 3
```

### 3b. Forex & Metals (rate limit 7/menit — biarkan jalan semalam)

```powershell
# XAUUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol XAUUSD --tf 1h --years 3
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol XAUUSD --tf 4h --years 3

# EURUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol EURUSD --tf 1h --years 3
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol EURUSD --tf 4h --years 3

# GBPUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol GBPUSD --tf 1h --years 3
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol GBPUSD --tf 4h --years 3

# USDJPY
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol USDJPY --tf 1h --years 3
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill --symbol USDJPY --tf 4h --years 3
```

### 3c. Verifikasi Backfill

```sql
SELECT i.symbol, c.timeframe, count(*)
FROM candles c
JOIN instruments i ON i.id = c.instrument_id
GROUP BY 1, 2
ORDER BY 1, 2;
```

Target: **≥ 18.000 baris 1h** per instrumen (≈3 tahun).

---

## 4. Kalender Ekonomi + Verifikasi Mapping

```powershell
# Sync sekali
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -c "import asyncio; from rtrade.data.finnhub_calendar import FinnhubCalendarProvider; p = FinnhubCalendarProvider('YOUR_FINNHUB_KEY'); print(asyncio.run(p.fetch_events()))"
```

Verifikasi di DB:
```sql
SELECT DISTINCT currency FROM economic_events LIMIT 20;
```

Harus muncul: `USD`, `EUR`, `GBP`, `JPY`. Kalau muncul kode lain (`MX`, `BR`), tambahkan di `_COUNTRY_TO_CURRENCY` jika relevan.

---

## 5. Backtest Pertama (setelah backfill selesai)

```powershell
# In-sample S1 × BTCUSDT (crypto paling cepat)
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT

# Walk-forward S1 × BTCUSDT
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward

# Smart-exit A/B
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward --smart-exit
```

Ulangi untuk semua kombinasi strategi × instrumen. Hasilnya di `reports/`.

---

## 6. Scan Manual Pertama

```powershell
# Via API (butuh server jalan)
curl -X POST http://localhost:8000/scan -H "Authorization: Bearer YOUR_API_AUTH_TOKEN"

# Atau langsung:
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -c "
import asyncio
from rtrade.pipeline.scan import run_scan
result = asyncio.run(run_scan())
print(result)
"
```

Periksa:
- Tabel `signals` terisi (status apa pun)
- `signal_audits` punya row `CANDIDATE`/`GATE`
- Log bersih (tidak ada exception)

---

## 7. Nyalakan LLM

Edit `config/settings.yaml`:
```yaml
llm:
  enabled: true
```

Jalankan scan manual lagi → cek audit stage `analyst` & `confidence` di payload.

---

## 8. Worker + Bot

```powershell
# Start scheduler (background)
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.scheduler.main

# Start Telegram bot (separate terminal)
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.bot
```

Tes Telegram:
- `/status` → harus respon sehat
- `/signals` → harus tampilkan sinyal terbaru (atau "no signals")

---

## 9. Checklist Post-Activation

- [ ] `GET /health` → semua OK
- [ ] Scheduler log: scan tiap jam tanpa exception
- [ ] Tabel `signals`: ada baris baru
- [ ] `signal_audits`: CANDIDATE/GATE/ANALYST/DELIVERY muncul
- [ ] `derivatives_snapshots`: terisi untuk BTC/ETH
- [ ] Paper tracker: signal status berubah wajar (FILLED→TP/SL/EXPIRED)
