# IMPLEMENTATION TASKS 5 — VPS DEPLOY READINESS (D1–D9)

> Hasil audit aset deploy (setup_vps.sh, docker-compose.prod.yml, Dockerfile, Caddyfile,
> .env.prod.example, runbook): fondasinya bagus (security, secrets auto-gen, logrotate, backup),
> TAPI ada **3 BLOCKER yang membuat deploy GAGAL hari ini**:
>
> 1. Service `api` menjalankan modul yang TIDAK ADA (`rtrade.api.main:app`) → container crash-loop.
> 2. Service `app` depends_on `litellm: service_healthy` — proxy litellm TIDAK dipakai aplikasi
>    (LLM jalan library-mode sejak F1) dan healthcheck-nya bisa gagal → `app` TIDAK PERNAH start.
> 3. `docs/RUNBOOK_ACTIVATION.md` memakai flag backfill yang SALAH (`--symbol/--tf/--years`),
>    CLI sebenarnya positional + `--days` → semua perintah backfill di runbook error.
>
> Ditambah: tidak ada service bot Telegram, HEALTHCHECK Dockerfile salah sasaran untuk
> scheduler, volume `models/` (HMM) tidak ada, Caddyfile tidak meng-cover route nyata.
>
> Aturan: sama dengan IMPLEMENTATION_TASKS.md §0. BUKTI pakai Select-String. Commit per task.
> JANGAN menyentuh logika trading — ini murni deploy/infra.

---

## D1 — FIX BLOCKER: modul API service salah

1. `src/rtrade/delivery/api/app.py` — tambah di akhir file:
   ```python
   # Module-level app for uvicorn (compose: rtrade.delivery.api.app:app).
   app = create_app()
   ```
2. `docker-compose.prod.yml` service `api` — ganti command:
   ```yaml
   command: ["python", "-m", "uvicorn", "rtrade.delivery.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
   ```

**Test**: `tests/unit/test_api_app.py` — `from rtrade.delivery.api.app import app` lalu
`assert app.title == "Robil Trade API"`.
**BUKTI**:
```powershell
Select-String -Path docker-compose.prod.yml -Pattern "rtrade.delivery.api.app:app"   # 1
Select-String -Path docker-compose.prod.yml -Pattern "rtrade.api.main"               # 0
```
**Commit**: `fix(deploy): api service points to real FastAPI module (D1)`

---

## D2 — FIX BLOCKER: hapus litellm proxy (aplikasi pakai library mode)

1. `docker-compose.prod.yml`:
   - HAPUS seluruh service `litellm`.
   - Di service `app` dan `api`: hapus `LITELLM_BASE_URL` dari environment dan hapus
     `litellm: condition: service_healthy` dari `depends_on`.
2. `src/rtrade/monitoring/healthcheck.py` — `check_litellm` jadi opsional:
   ```python
   async def check_litellm(self) -> CheckResult | None:
       """Skip entirely when no proxy is configured (library mode)."""
       if not self._litellm_url:
           return None
       ...
   ```
   dan di `run_all()`:
   ```python
   litellm_check = await self.check_litellm()
   if litellm_check is not None:
       checks.append(litellm_check)
   ```
3. Semua pemanggil `HealthChecker(...)` (`delivery/api/routes.py`, `scheduler/jobs.py`) —
   ganti `litellm_url=cfg.secrets.litellm_base_url` menjadi `litellm_url=""`
   (library mode; proxy tidak dipakai). JANGAN hapus field `litellm_base_url` dari Secrets
   (kompatibilitas .env lama).
4. `.env.prod.example` — beri komentar pada blok LITELLM:
   ```
   # LITELLM_MASTER_KEY / LITELLM_BASE_URL tidak dipakai (LLM = library mode sejak F1).
   ```
5. `scripts/setup_vps.sh` — di heredoc .env: hapus baris `LITELLM_BASE_URL=http://litellm:4000`
   (ganti dengan komentar yang sama), dan turunkan ekspektasi service sehat dari `-ge 5`
   sesuai jumlah service final (lihat D3 — total 6 service tanpa bot, 7 dengan bot).

**Test**: di `tests/unit/test_alerts.py` atau file healthcheck test —
`HealthChecker(litellm_url="").run_all()` TIDAK berisi check bernama "litellm";
dengan url terisi (mock httpx error) → berisi check litellm DEGRADED.
**BUKTI**:
```powershell
Select-String -Path docker-compose.prod.yml -Pattern "litellm"   # 0
Select-String -Path src\rtrade\monitoring\healthcheck.py -Pattern "if not self._litellm_url"  # 1
```
**Commit**: `fix(deploy): drop unused litellm proxy service; optional litellm health check (D2)`

---

## D3 — Service bot Telegram + healthcheck per-service yang benar

1. **Dockerfile** — HAPUS blok `HEALTHCHECK` global (curl :8000 hanya valid untuk api;
   container scheduler/bot akan selamanya "unhealthy"). `EXPOSE 8000` boleh tetap.
2. `docker-compose.prod.yml`:
   - Service `api` — tambah healthcheck eksplisit:
     ```yaml
     healthcheck:
       test: ["CMD", "curl", "-sf", "http://localhost:8000/health"]
       interval: 30s
       timeout: 5s
       retries: 3
       start_period: 15s
     ```
   - Service `app` (scheduler) — tambah healthcheck ringan (proses hidup + import OK):
     ```yaml
     healthcheck:
       test: ["CMD", "python", "-c", "import rtrade.scheduler.main"]
       interval: 60s
       timeout: 10s
       retries: 3
     ```
   - Service BARU `bot` (polling Telegram) di bawah profile supaya tidak crash-loop
     saat token kosong:
     ```yaml
     bot:
       build:
         context: .
         dockerfile: Dockerfile
       restart: unless-stopped
       profiles: ["telegram"]
       env_file: .env
       environment:
         ENV: prod
         LOG_LEVEL: INFO
         DATABASE_URL: postgresql+asyncpg://rtrade:${RTRADE_DB_PASSWORD}@db:5432/rtrade
         REDIS_URL: redis://redis:6379/0
       command: ["python", "-m", "rtrade.cli.bot"]
       networks:
         - internal
       depends_on:
         db:
           condition: service_healthy
       deploy:
         resources:
           limits:
             memory: 512M
     ```
3. `src/rtrade/cli/bot.py` — guard token kosong (jangan crash-loop):
   ```python
   if not cfg.secrets.telegram_bot_token or not cfg.secrets.telegram_chat_id:
       logger.error("TELEGRAM_BOT_TOKEN/CHAT_ID kosong — bot tidak dijalankan")
       return
   ```
4. `scripts/setup_vps.sh` — saat start services, jika `TELEGRAM_TOKEN` terisi gunakan
   `--profile telegram`:
   ```bash
   local profiles=""
   [[ -n "$TELEGRAM_TOKEN" ]] && profiles="--profile telegram"
   sudo -u "$APP_USER" docker compose -f docker-compose.yml -f docker-compose.prod.yml $profiles up -d
   ```
   (terapkan di `build_and_start` dan restart pasca-logrotate; simpan pilihan profile di
   variabel global script).

**BUKTI**:
```powershell
Select-String -Path Dockerfile -Pattern "HEALTHCHECK"                       # 0
Select-String -Path docker-compose.prod.yml -Pattern "rtrade.cli.bot"      # 1
Select-String -Path docker-compose.prod.yml -Pattern "profiles"            # >= 1
```
**Commit**: `feat(deploy): telegram bot service + correct per-service healthchecks (D3)`

---

## D4 — Volume models/ (HMM) + direktori runtime

1. `docker-compose.prod.yml` service `app` — tambah volume `- ./models:/app/models`.
   (api & bot TIDAK butuh models.)
2. `Dockerfile` — di `RUN mkdir -p ...` tambahkan `/app/models`.
3. `.gitignore` — tambah baris `models/` (kalau belum).
4. `scripts/setup_vps.sh` — `mkdir -p "$INSTALL_DIR"/{data,reports,logs,models}` (ganti baris yang ada).

**BUKTI**: `Select-String -Path docker-compose.prod.yml -Pattern "./models:/app/models"` = 1.
**Commit**: `feat(deploy): persist HMM models via volume (D4)`

---

## D5 — Caddyfile: lindungi route yang BENAR-BENAR ada

Route nyata API saat ini: `/health`, `/metrics`, `/signals`, `/signals/{id}`, `/calibration`,
`/scan` (POST), `/analytics/*`, `/strategies/{name}/enable|disable`. Caddyfile lama hanya
meng-cover sebagian (`/api/*` tidak ada di aplikasi).

GANTI isi blok utama `config/Caddyfile` dengan pola "deny by default, health publik":
```caddyfile
{$DOMAIN:localhost} {
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }

    # Publik: health saja.
    handle /health {
        reverse_proxy api:8000
    }

    # SEMUA route lain wajib bearer token.
    handle {
        @no_auth {
            not header Authorization "Bearer {$API_AUTH_TOKEN:changeme}"
        }
        respond @no_auth 401 {
            body "Unauthorized"
            close
        }
        reverse_proxy api:8000
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size 50MiB
            roll_keep 5
        }
        format json
    }
}
```
CATATAN: `caddy` container butuh env `API_AUTH_TOKEN` dan `DOMAIN` — tambahkan di
`docker-compose.prod.yml` service caddy:
```yaml
environment:
  DOMAIN: ${DOMAIN:-localhost}
  API_AUTH_TOKEN: ${API_AUTH_TOKEN:-changeme}
```

**BUKTI**: `Select-String -Path config\Caddyfile -Pattern "/api/"` = 0;
`Select-String -Path docker-compose.prod.yml -Pattern "API_AUTH_TOKEN"` >= 1.
**Commit**: `fix(deploy): Caddyfile deny-by-default matching real routes (D5)`

---

## D6 — FIX BLOCKER: perintah backfill di runbook salah + script backfill massal

CLI nyata: `python -m rtrade.cli.backfill SYMBOL TIMEFRAME --days N` (positional!).
1. `docs/RUNBOOK_ACTIVATION.md` — perbaiki SEMUA perintah backfill ke bentuk benar, dan tambah
   versi VPS (di dalam container):
   ```bash
   # Di VPS (dari /opt/robil-trade):
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     exec app python -m rtrade.cli.backfill BTCUSDT 1h --days 1095
   ```
2. Script baru `scripts/backfill_all.sh` (dipakai di VPS):
   ```bash
   #!/usr/bin/env bash
   # Backfill semua instrumen. Crypto dulu (cepat), forex/metals belakangan (rate-limit 7/menit).
   set -uo pipefail
   COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
   DAYS="${1:-1095}"
   run() {
       echo "=== backfill $1 $2 (${DAYS}d) ==="
       $COMPOSE exec -T app python -m rtrade.cli.backfill "$1" "$2" --days "$DAYS" \
           || echo "WARN: backfill $1 $2 gagal — lanjut"
   }
   for tf in 1h 4h; do run BTCUSDT $tf; run ETHUSDT $tf; done
   for tf in 1h 4h; do
       run XAUUSD $tf; run EURUSD $tf; run GBPUSD $tf; run USDJPY $tf
   done
   echo "=== selesai. Verifikasi: ==="
   $COMPOSE exec -T db psql -U rtrade -d rtrade -c \
     "SELECT i.symbol, c.timeframe, count(*) FROM candles c JOIN instruments i ON i.id=c.instrument_id GROUP BY 1,2 ORDER BY 1,2;"
   ```
   `chmod +x` via git (`git update-index --chmod=+x` tidak perlu di Windows — cukup catat di
   runbook: `chmod +x scripts/*.sh` ada di setup_vps).
3. Runbook tambah section "Validasi di VPS":
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
     exec app python scripts/run_backtest.py --strategy s1_trend_pullback --instrument BTCUSDT --walkforward --smart-exit
   # hasil ada di ./reports (volume-mounted ke host)
   ```

**BUKTI**:
```powershell
Select-String -Path docs\RUNBOOK_ACTIVATION.md -Pattern "--symbol|--tf |--years"  # 0
Test-Path scripts\backfill_all.sh                                                  # True
```
**Commit**: `fix(ops): correct backfill commands + bulk backfill script for VPS (D6)`

---

## D7 — setup_vps.sh: sinkron dengan stack final

1. Step 6 `build_and_start`: profile telegram kondisional (lihat D3.4).
2. Step 6 wait-loop: jumlah service final tanpa bot = 6 (db, redis, app, api, caddy, backup);
   dengan bot = 7. Set threshold dinamis atau `-ge 6`.
3. Step 5 heredoc .env: hapus `LITELLM_BASE_URL=http://litellm:4000` (D2), tambah baris:
   ```
   # === Trading config (opsional, default aman) ===
   # llm.enabled diatur via config/settings.yaml, bukan .env
   ```
4. Step 7 migrasi: `exec -T app python -m alembic upgrade head` → pastikan tetap jalan
   (alembic ada di venv image). Tambah retry 1× bila gagal pertama (db baru siap).
5. Step 9 summary: tambah baris next-steps:
   ```
   5. Backfill data : ./scripts/backfill_all.sh
   6. Validasi      : docker compose ... exec app python scripts/run_backtest.py ... --walkforward
   ```
6. Tambah step verifikasi kecil setelah migrasi: hitung tabel —
   `exec -T db psql -U rtrade -d rtrade -c "\dt"` dan tampilkan.

**BUKTI**: `Select-String -Path scripts\setup_vps.sh -Pattern "backfill_all|profile telegram|-ge 6"` >= 2.
**Commit**: `feat(ops): setup_vps.sh aligned with final service stack (D7)`

---

## D8 — Smoke test deploy LOKAL (wajib sebelum dianggap siap VPS)

Jalankan di mesin ini (docker tersedia) dengan .env dummy — TANPA API key nyata:
1. `Copy-Item .env.prod.example .env.smoke` lalu isi minimal:
   `RTRADE_DB_PASSWORD=smoketest123`, `API_AUTH_TOKEN=smoketoken`, sisanya kosong.
2. Build & up (tanpa profile telegram):
   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.smoke build
   docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.smoke up -d db redis app api caddy
   ```
3. Verifikasi (laporkan output mentah):
   - `docker compose ... ps` → db/redis/api healthy, app running.
   - `docker compose ... exec -T app python -m alembic upgrade head` → sukses.
   - `curl http://localhost/health` (via caddy, DOMAIN=localhost → HTTP) → JSON status.
   - `curl http://localhost/signals` tanpa header → 401; dengan
     `-H "Authorization: Bearer smoketoken"` → 200.
   - Log app: scheduler start + daftar job (`docker compose ... logs app --tail 30`).
4. Teardown: `docker compose ... down -v` + hapus `.env.smoke` (JANGAN commit).
5. Bila ada kegagalan → perbaiki → ulangi sampai semua hijau, BARU tandai selesai.

**BUKTI**: lampirkan output `ps`, `curl /health`, dan 401/200 di laporan.
**Commit**: `test(deploy): local prod-stack smoke verified (D8)` (commit hanya fix yang muncul;
artefak smoke JANGAN di-commit)

---

## D9 — Refresh runbook deploy + dokumen serah-terima VPS

`docs/runbooks/deploy.md` — tulis ulang ringkas sesuai realita final:
1. **Sekali jalan**: `curl -sSL .../setup_vps.sh | sudo bash` (atau clone manual + sudo ./scripts/setup_vps.sh),
   isi API key saat diminta → stack hidup.
2. **Pasca-setup**: `./scripts/backfill_all.sh` → validasi walk-forward (perintah exec) →
   isi `docs/VALIDATION_RESULTS.md` → set `llm.enabled: true` di config/settings.yaml →
   `sudo ./scripts/update.sh` (rebuild+restart).
3. **Operasi harian**: `make prod-logs`, `make prod-health`, `./scripts/status.sh`,
   backup otomatis 03:00 UTC (cek `make backup-list`).
4. **Update kode**: `sudo ./scripts/update.sh`.
5. **Rollback**: lihat `docs/runbooks/rollback.md` (pastikan masih akurat — koreksi bila perlu).
6. Matriks port: 80/443 publik (caddy), 8000 internal-only, db/redis internal-only.

**BUKTI**: `Select-String -Path docs\runbooks\deploy.md -Pattern "backfill_all|setup_vps"` >= 2.
**Commit**: `docs(ops): deploy runbook refreshed for final stack (D9)`

---

## CHECKLIST AKHIR
```powershell
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run ruff check src tests
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run mypy
& "C:\Users\Dian Ganteng\.local\bin\uv.exe" run pytest -q
docker compose -f docker-compose.yml -f docker-compose.prod.yml config > $null; $LASTEXITCODE  # 0 = compose valid
```
Laporan: per task D → status + output BUKTI mentah + (D8) bukti smoke test lengkap.
Setelah D1–D9 hijau: bot SIAP di-deploy — user tinggal jalankan setup_vps.sh di VPS.
