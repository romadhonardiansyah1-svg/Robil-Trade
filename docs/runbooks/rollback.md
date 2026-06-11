# Runbook: Rollback

## Kapan Rollback

- Deploy baru menyebabkan crash/error
- Health check gagal setelah deploy
- Regression pada fitur penting

## Prosedur Rollback

### 1. Rollback Cepat (Image Sebelumnya)

```bash
ssh robil-vps
cd /opt/robil-trade

# Lihat image history
docker images robil-trade-app --format "table {{.ID}}\t{{.CreatedAt}}\t{{.Size}}"

# Stop service bermasalah
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop app api

# Rollback ke commit sebelumnya
git log --oneline -5
git checkout <commit-sebelumnya>

# Rebuild dan restart
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build app api
```

### 2. Rollback Database Migration

```bash
# Lihat migration history
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python -m alembic history

# Rollback 1 step
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python -m alembic downgrade -1

# Rollback ke revision spesifik
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app \
  python -m alembic downgrade <revision_id>
```

### 3. Restore Database dari Backup

```bash
# Lihat backup tersedia
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backup \
  ls -lh /backups/

# Stop app terlebih dahulu
docker compose -f docker-compose.yml -f docker-compose.prod.yml stop app api

# Restore
gunzip -c /path/to/backup/rtrade_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  psql -U rtrade -d rtrade

# Restart app
docker compose -f docker-compose.yml -f docker-compose.prod.yml start app api
```

## Verifikasi Setelah Rollback

- [ ] Health check hijau
- [ ] Telegram bot merespons `/health`
- [ ] Log tidak ada error baru
- [ ] Scan berikutnya berhasil

## Catatan

- **JANGAN** rollback database tanpa stop app terlebih dahulu
- **SELALU** backup database sebelum rollback migration
- Catat alasan rollback di ADR baru jika terkait perubahan arsitektur
