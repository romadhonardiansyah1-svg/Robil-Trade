# Runbook: Deploy Robil Trade ke VPS

## 🚀 Quick Deploy (Hermes-Style — 1 Command)

### Setup Pertama Kali

SSH ke VPS, lalu jalankan:

```bash
ssh robil-vps

# Download dan jalankan setup script
curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh -o /tmp/setup_vps.sh
chmod +x /tmp/setup_vps.sh
sudo /tmp/setup_vps.sh
```

Script ini akan **otomatis**:
1. ✅ Cek system requirements (CPU/RAM/Disk)
2. ✅ Install Docker, UFW, fail2ban
3. ✅ Configure firewall (SSH + HTTP/HTTPS only)
4. ✅ Buat user `rtrade` + direktori
5. ✅ Clone repo dari GitHub
6. ✅ Prompt untuk API keys (auto-generate secrets)
7. ✅ Build & start semua 7 Docker containers
8. ✅ Run database migrations
9. ✅ Setup log rotation
10. ✅ Verify semua service healthy

### Update (Setelah Setup Awal)

```bash
ssh robil-vps
cd /opt/robil-trade
sudo ./scripts/update.sh
```

### Cek Status

```bash
ssh robil-vps
cd /opt/robil-trade
./scripts/status.sh
```

---

## 📋 Manual Deploy (Step-by-Step)

Jika lebih suka manual, ikuti langkah di bawah.

### Prerequisites

- VPS Ubuntu 24.04 (4 vCPU / 8GB RAM / 190GB disk)
- Docker + Docker Compose terinstall
- SSH access: `ssh robil-vps`
- Domain (opsional, untuk TLS auto)

### 1. Persiapan Server (Sekali)

```bash
ssh robil-vps

# Update sistem
apt update && apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh

# Firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw enable

# User non-root
adduser rtrade --disabled-password
usermod -aG docker rtrade

# Buat direktori
mkdir -p /opt/robil-trade
chown rtrade:rtrade /opt/robil-trade
```

### 2. Deploy

```bash
# Clone repo
git clone https://github.com/romadhonardiansyah1-svg/Robil-Trade.git /opt/robil-trade
cd /opt/robil-trade

# Buat .env dari template
cp .env.prod.example .env
nano .env  # Isi semua credentials
chmod 600 .env

# Build dan start
make deploy
```

### 3. Verifikasi

```bash
# Status semua container
make prod-ps

# Health check
make prod-health

# Logs
make prod-logs

# Kirim /health di Telegram
```

---

## 🔧 Useful Commands

| Command | Deskripsi |
|---------|-----------|
| `make prod` | Start/rebuild production stack |
| `make prod-down` | Stop semua services |
| `make prod-logs` | Tail logs (semua services) |
| `make prod-ps` | Status containers |
| `make prod-health` | API health check |
| `make deploy` | Full deploy: build + start + migrate |
| `make backup` | Trigger manual backup |
| `./scripts/status.sh` | Full system diagnostic |
| `./scripts/update.sh` | Pull + rebuild + restart |

---

## 🚨 Troubleshooting

| Masalah | Solusi |
|---------|--------|
| Container restart loop | `docker logs <container>` — cek error |
| DB connection refused | Pastikan db container healthy, cek `DATABASE_URL` |
| LiteLLM unhealthy | Cek API key valid, `docker logs litellm` |
| Port 443 tidak bisa bind | Pastikan Caddy punya akses, cek `ufw status` |
| Out of memory | `docker stats` — cek memory limits di compose |
| Permission denied | Pastikan user `rtrade` di group `docker` |
