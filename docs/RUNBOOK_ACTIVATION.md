# RUNBOOK: Aktivasi Infrastruktur Robil Trade (V4 / D6)

> **PENTING**: File ini adalah instruksi untuk USER. Jangan commit `.env` atau secret apa pun ke Git.

---

## 1. Environment Variables (`.env`)

Buat file `.env` di root project:

```dotenv
# Database
RTRADE_DB_PASSWORD=your_strong_password_here
DATABASE_URL=postgresql+asyncpg://rtrade:YOUR_PASSWORD@db:5432/rtrade
REDIS_URL=redis://redis:6379/0

# Data Providers
TWELVEDATA_API_KEY=your_twelvedata_key_here
FINNHUB_API_KEY=your_finnhub_key_here

# LLM (library mode — no proxy needed)
GEMINI_API_KEY_1=your_gemini_api_key_here

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# API Auth
API_AUTH_TOKEN=your_random_auth_token_here
DOMAIN=localhost
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
# BTCUSDT 1h, 3 tahun (1095 hari)
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill BTCUSDT 1h --days 1095

# BTCUSDT 4h
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill BTCUSDT 4h --days 1095

# ETHUSDT 1h + 4h
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill ETHUSDT 1h --days 1095
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill ETHUSDT 4h --days 1095
```

### 3b. Forex & Metals (rate limit 7/menit — biarkan jalan semalam)

```powershell
# XAUUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill XAUUSD 1h --days 1095
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill XAUUSD 4h --days 1095

# EURUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill EURUSD 1h --days 1095
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill EURUSD 4h --days 1095

# GBPUSD
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill GBPUSD 1h --days 1095
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill GBPUSD 4h --days 1095

# USDJPY
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill USDJPY 1h --days 1095
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.backfill USDJPY 4h --days 1095
```

### 3c. Backfill di VPS (via Docker)

```bash
# Di VPS (dari /opt/robil-trade):
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec app python -m rtrade.cli.backfill BTCUSDT 1h --days 1095

# Atau gunakan script massal:
chmod +x scripts/backfill_all.sh
./scripts/backfill_all.sh
```

### 3d. Verifikasi Backfill

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

Harus muncul: `USD`, `EUR`, `GBP`, `JPY`.

---

## 5. Backtest (setelah backfill selesai)

```powershell
# In-sample S1 × BTCUSDT
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT

# Walk-forward S1 × BTCUSDT
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward

# Smart-exit A/B
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward --smart-exit
```

### Validasi di VPS

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec app python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward --smart-exit
# hasil ada di ./reports (volume-mounted ke host)
```

---

## 6. Scan Manual Pertama

```powershell
curl -X POST http://localhost:8000/scan -H "Authorization: Bearer YOUR_API_AUTH_TOKEN"
```

Periksa:
- Tabel `signals` terisi (status apa pun)
- `signal_audits` punya row `CANDIDATE`/`GATE`
- Log bersih

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
# Start scheduler
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.scheduler.main

# Start Telegram bot (separate terminal)
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run python -m rtrade.cli.bot
```

### Di VPS (dengan Docker)

```bash
# Tanpa Telegram:
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Dengan Telegram:
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram up -d
```

Tes Telegram: `/status`, `/signals`

---

## 9. Checklist Post-Activation

- [ ] `GET /health` → semua OK
- [ ] Scheduler log: scan tiap jam tanpa exception
- [ ] Tabel `signals`: ada baris baru
- [ ] `signal_audits`: CANDIDATE/GATE/ANALYST/DELIVERY muncul
- [ ] `derivatives_snapshots`: terisi untuk BTC/ETH
- [ ] Paper tracker: signal status berubah wajar (FILLED→TP/SL/EXPIRED)
