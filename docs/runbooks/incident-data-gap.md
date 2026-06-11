# Runbook: Incident — Data Gap

## Definisi

Data gap terjadi ketika candle OHLCV hilang dari database untuk periode tertentu.
Bisa disebabkan oleh: provider down, network issue, VPS restart, atau bug ingestion.

## Deteksi

1. **Alert otomatis**: `PROVIDER_DOWN` alert setelah 15 menit
2. **Manual check**: Missing candles di scan log
3. **Health check**: Provider freshness gauge di `/metrics`

## Severity Assessment

| Gap Duration | Severity | Impact |
|-------------|----------|--------|
| < 1 jam | Low | 1-2 candle 1H hilang, sinyal mungkin tertunda |
| 1-4 jam | Medium | Indikator mungkin tidak akurat (lookback terganggu) |
| 4-24 jam | High | Regime detection bisa salah, block sinyal |
| > 24 jam | Critical | Backfill manual diperlukan |

## Prosedur Penanganan

### 1. Identifikasi Gap

```bash
ssh robil-vps
cd /opt/robil-trade

# Cek log provider
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=100 app | grep -i "error\|gap\|fail"

# Query database untuk gap
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT i.symbol, c.timeframe, MAX(c.ts) as last_candle,
           NOW() - MAX(c.ts) as gap_duration
    FROM candles c
    JOIN instruments i ON i.id = c.instrument_id
    GROUP BY i.symbol, c.timeframe
    ORDER BY gap_duration DESC;
  "
```

### 2. Tentukan Penyebab

| Penyebab | Tanda | Solusi |
|----------|-------|--------|
| Provider down | HTTP error di log | Tunggu pulih, backfill setelah |
| Rate limit (429) | Rate limit di log | Key rotation / tunggu cooldown |
| VPS restart | Container restart di log | Otomatis backfill saat start |
| Bug ingestion | Exception di log | Fix bug, deploy, backfill |

### 3. Backfill

```bash
# Backfill instrumen spesifik
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python -m rtrade.scripts.backfill --instrument XAUUSD --from 2026-06-01

# Backfill semua
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python -m rtrade.scripts.backfill --all --from 2026-06-01
```

### 4. Verifikasi

```bash
# Re-check gap
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec db \
  psql -U rtrade -d rtrade -c "
    SELECT i.symbol, c.timeframe, COUNT(*) as candle_count,
           MIN(c.ts) as first, MAX(c.ts) as last
    FROM candles c
    JOIN instruments i ON i.id = c.instrument_id
    WHERE c.ts > NOW() - INTERVAL '7 days'
    GROUP BY i.symbol, c.timeframe;
  "
```

### 5. Post-Incident

- [ ] Gap terisi lengkap
- [ ] Indikator dan regime stabil kembali
- [ ] Scan berikutnya berjalan normal
- [ ] Dokumentasi di log/ADR jika penyebab baru

## Fail-Safe

Jika gap tidak bisa diisi (provider data tidak tersedia):
- **Scheduler TETAP jalan** — scan akan skip bar yang tidak ada data
- **Sinyal TIDAK terbit** untuk instrumen yang datanya stale (>2× TF)
- Ini adalah perilaku yang BENAR: lebih baik tidak ada sinyal daripada sinyal berdasar data tidak lengkap
