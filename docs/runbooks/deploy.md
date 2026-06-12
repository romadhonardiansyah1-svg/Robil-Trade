# Deploy Runbook — Robil Trade (D9)

> Semua langkah sesuai stack final pasca-D1–D8. Terakhir diperbarui: 2026-06-12.

---

## 1. Setup Awal (Sekali Jalan)

### Opsi A: Auto-setup (recommended)
```bash
curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh | sudo bash
```

### Opsi B: Manual
```bash
git clone --depth 1 https://github.com/romadhonardiansyah1-svg/Robil-Trade.git /opt/robil-trade
cd /opt/robil-trade
sudo ./scripts/setup_vps.sh
```

Script akan:
1. Cek prerequisites (Docker, CPU, RAM)
2. Setup firewall (SSH + HTTP/HTTPS)
3. Clone repo + buat user `rtrade`
4. Generate secrets → `.env` (chmod 600)
5. Build & start semua container
6. Jalankan Alembic migration
7. Tampilkan status service

---

## 2. Pasca-Setup

### Backfill data
```bash
chmod +x scripts/*.sh
./scripts/backfill_all.sh
```

### Validasi walk-forward
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec app python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward --smart-exit
```

Isi hasil di `docs/VALIDATION_RESULTS.md`.

### Nyalakan LLM
```bash
# Edit config/settings.yaml → llm.enabled: true
sudo ./scripts/update.sh
```

---

## 3. Operasi Harian

| Perintah | Fungsi |
|----------|--------|
| `make prod-logs` | Lihat log semua service |
| `make prod-health` | Cek `/health` endpoint |
| `./scripts/status.sh` | Status container |
| `make backup-list` | Lihat daftar backup (auto 03:00 UTC) |

---

## 4. Update Kode

```bash
sudo ./scripts/update.sh
```
Script ini: `git pull` → `docker compose build` → `docker compose up -d` → migrations.

---

## 5. Rollback

Lihat `docs/runbooks/rollback.md`.

---

## 6. Matriks Port

| Port | Akses | Service |
|------|-------|---------|
| 80/443 | Publik (Caddy) | API via reverse proxy |
| 8000 | Internal only | uvicorn (api container) |
| 5432 | Internal only | PostgreSQL |
| 6379 | Internal only | Redis |

---

## 7. Service Stack

| Service | Container | Profile | Healthcheck |
|---------|-----------|---------|-------------|
| db | timescaledb:pg16 | default | pg_isready |
| redis | redis:7-alpine | default | redis-cli ping |
| app | rtrade (scheduler) | default | `python -c "import rtrade.scheduler.main"` |
| api | rtrade (uvicorn) | default | curl /health |
| caddy | caddy:2-alpine | default | — |
| backup | postgres:16-alpine | default | — |
| bot | rtrade (telegram) | `telegram` | — |

Tanpa Telegram token → 6 service. Dengan token → `--profile telegram` → 7 service.
