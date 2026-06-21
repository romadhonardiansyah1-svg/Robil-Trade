#!/usr/bin/env bash
# ============================================================================
#  Robil Trade — VPS Auto Setup Script (Hermes-style)
#  
#  Jalankan di VPS Ubuntu 24.04 (download dulu — JANGAN curl|bash, prompt butuh stdin):
#    curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh -o setup_vps.sh
#    chmod +x setup_vps.sh
#    sudo ./setup_vps.sh
#
#  Script ini akan:
#   1. Cek dan install semua prerequisites (Docker, UFW, dll)
#   2. Buat user & direktori
#   3. Clone repo dari GitHub
#   4. Generate semua secrets otomatis
#   5. Buat file .env production
#   6. Build & start semua container
#   7. Jalankan database migration
#   8. Verifikasi semua service healthy
#   9. Tampilkan summary lengkap
#
#  ⚠️  Jalankan sebagai ROOT atau dengan sudo.
# ============================================================================

set -euo pipefail

# ============================================================================
# KONFIGURASI — Edit sesuai kebutuhan
# ============================================================================
REPO_URL="https://github.com/romadhonardiansyah1-svg/Robil-Trade.git"
INSTALL_DIR="/opt/robil-trade"
APP_USER="rtrade"
BRANCH="main"

# Minimum requirements
MIN_RAM_MB=4096
MIN_DISK_GB=30
MIN_CPUS=2

# ============================================================================
# WARNA & HELPERS
# ============================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m'  # No Color
BOLD='\033[1m'

banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}${MAGENTA}🤖 ROBIL TRADE — VPS Auto Setup${NC}                             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}AI-Powered Trading Signal Engine${NC}                           ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
success() { echo -e "${GREEN}[✅ OK]${NC}   $1"; }
warn()    { echo -e "${YELLOW}[⚠️  WARN]${NC} $1"; }
error()   { echo -e "${RED}[❌ ERROR]${NC} $1"; }
step()    { echo -e "\n${BOLD}${MAGENTA}━━━ STEP $1 ━━━${NC}"; }
divider() { echo -e "${CYAN}──────────────────────────────────────────────────────────────${NC}"; }

confirm() {
    local prompt="$1"
    local default="${2:-y}"
    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$(echo -e "${YELLOW}$prompt [Y/n]:${NC} ")" yn
        yn="${yn:-y}"
    else
        read -rp "$(echo -e "${YELLOW}$prompt [y/N]:${NC} ")" yn
        yn="${yn:-n}"
    fi
    [[ "$yn" =~ ^[Yy] ]]
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        error "Script ini harus dijalankan sebagai root!"
        echo "  Gunakan: sudo $0"
        exit 1
    fi
}

# ============================================================================
# STEP 1: SYSTEM CHECK
# ============================================================================
check_system() {
    step "1/9 — System Requirements Check"
    
    local errors=0
    
    # OS Check
    if [[ -f /etc/os-release ]]; then
        . /etc/os-release
        info "OS: $PRETTY_NAME"
        if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
            warn "OS bukan Ubuntu/Debian. Script mungkin perlu penyesuaian."
        fi
    fi
    
    # CPU Check
    local cpus
    cpus=$(nproc)
    if [[ $cpus -ge $MIN_CPUS ]]; then
        success "CPU: ${cpus} cores (minimum: ${MIN_CPUS})"
    else
        error "CPU: ${cpus} cores (minimum: ${MIN_CPUS})"
        ((errors++))
    fi
    
    # RAM Check
    local ram_mb
    ram_mb=$(free -m | awk '/^Mem:/{print $2}')
    if [[ $ram_mb -ge $MIN_RAM_MB ]]; then
        success "RAM: ${ram_mb}MB (minimum: ${MIN_RAM_MB}MB)"
    else
        error "RAM: ${ram_mb}MB (minimum: ${MIN_RAM_MB}MB)"
        ((errors++))
    fi
    
    # Disk Check
    local disk_gb
    disk_gb=$(df -BG / | awk 'NR==2{gsub(/G/,"",$4); print $4}')
    if [[ $disk_gb -ge $MIN_DISK_GB ]]; then
        success "Disk free: ${disk_gb}GB (minimum: ${MIN_DISK_GB}GB)"
    else
        error "Disk free: ${disk_gb}GB (minimum: ${MIN_DISK_GB}GB)"
        ((errors++))
    fi
    
    if [[ $errors -gt 0 ]]; then
        error "System tidak memenuhi requirements minimum!"
        if ! confirm "Lanjutkan meskipun requirements tidak terpenuhi?" "n"; then
            exit 1
        fi
    else
        success "Semua system requirements terpenuhi ✓"
    fi
}

# ============================================================================
# STEP 2: INSTALL PREREQUISITES
# ============================================================================
install_prerequisites() {
    step "2/9 — Install Prerequisites"
    
    info "Updating package list..."
    apt-get update -qq
    
    info "Installing base packages..."
    apt-get install -y -qq \
        curl \
        git \
        wget \
        unzip \
        jq \
        htop \
        ufw \
        fail2ban \
        logrotate \
        > /dev/null 2>&1
    success "Base packages installed"
    
    # Docker
    if command -v docker &> /dev/null; then
        local docker_version
        docker_version=$(docker --version | awk '{print $3}' | tr -d ',')
        success "Docker already installed: v${docker_version}"
    else
        info "Installing Docker..."
        # Hardening (E6): never pipe a remote script straight into a shell
        # (`curl ... | sh` is unverified remote code execution). Download
        # Docker's official installer to a local file first so it can be
        # inspected/audited, then execute the local copy. This preserves the
        # provisioning flow (installs docker-ce + the compose v2 plugin that
        # the check below relies on) while removing the curl|sh RCE path.
        local docker_install
        docker_install="$(mktemp)"
        curl -fsSL https://get.docker.com -o "$docker_install"
        sh "$docker_install" > /dev/null 2>&1
        rm -f "$docker_install"
        success "Docker installed: $(docker --version | awk '{print $3}' | tr -d ',')"
    fi
    
    # Docker Compose (v2 plugin)
    if docker compose version &> /dev/null; then
        success "Docker Compose: $(docker compose version --short)"
    else
        error "Docker Compose plugin tidak tersedia!"
        exit 1
    fi
    
    # Start Docker
    systemctl enable docker
    systemctl start docker
    success "Docker daemon running"
}

# ============================================================================
# STEP 3: SECURITY SETUP
# ============================================================================
setup_security() {
    step "3/9 — Security Setup"
    
    # UFW Firewall
    info "Configuring firewall (UFW)..."
    ufw --force reset > /dev/null 2>&1
    ufw default deny incoming > /dev/null 2>&1
    ufw default allow outgoing > /dev/null 2>&1
    ufw allow OpenSSH > /dev/null 2>&1
    ufw allow 80/tcp > /dev/null 2>&1
    ufw allow 443/tcp > /dev/null 2>&1
    ufw --force enable > /dev/null 2>&1
    success "Firewall configured: SSH + HTTP/HTTPS"
    
    # Fail2Ban
    if systemctl is-active --quiet fail2ban; then
        success "Fail2Ban already running"
    else
        systemctl enable fail2ban > /dev/null 2>&1
        systemctl start fail2ban > /dev/null 2>&1
        success "Fail2Ban enabled"
    fi
    
    # Create app user
    if id "$APP_USER" &>/dev/null; then
        success "User '$APP_USER' already exists"
    else
        info "Creating user '$APP_USER'..."
        useradd --create-home --shell /bin/bash "$APP_USER"
        usermod -aG docker "$APP_USER"
        success "User '$APP_USER' created and added to docker group"
    fi
    
    # Ensure docker group membership
    usermod -aG docker "$APP_USER" 2>/dev/null || true
}

# ============================================================================
# STEP 4: CLONE REPOSITORY
# ============================================================================
clone_repo() {
    step "4/9 — Clone Repository"
    
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Repository exists. Pulling latest..."
        cd "$INSTALL_DIR"
        git fetch origin
        git reset --hard "origin/$BRANCH"
        success "Repository updated to latest $BRANCH"
    else
        info "Cloning from $REPO_URL..."
        rm -rf "$INSTALL_DIR"
        git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
        success "Repository cloned to $INSTALL_DIR"
    fi
    
    # Set ownership
    chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
    
    # Create data directories
    mkdir -p "$INSTALL_DIR"/{data,reports,logs,models}
    chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"/{data,reports,logs,models}
    success "Directories created"
}

# ============================================================================
# STEP 5: COLLECT CREDENTIALS — secrets + data providers + Telegram (LLM via wizard)
# ============================================================================

collect_credentials() {
    step "5/9 — Credentials & Configuration"

    local DB_PASSWORD AUTH_TOKEN TOKEN_KEY
    DB_PASSWORD=$(openssl rand -hex 24)
    AUTH_TOKEN=$(openssl rand -hex 32)
    # RTRADE_TOKEN_KEY = Fernet key (urlsafe base64, 32 byte). Wajib di prod (C2/F1).
    TOKEN_KEY=$(python3 - <<'PYEOF' 2>/dev/null || openssl rand -base64 32 | tr '+/' '-_'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PYEOF
)
    info "Secrets auto-generated ✓ (termasuk RTRADE_TOKEN_KEY untuk token OAuth)"

    local TWELVEDATA_KEY="" FINNHUB_KEY="" DOMAIN=""
    local OANDA_TOKEN="" OANDA_ACCOUNT="" OANDA_ENVIRONMENT=""
    TELEGRAM_TOKEN=""; TELEGRAM_CHAT=""   # global: dipakai step 6 & 8
    echo ""
    echo -e "${BOLD}--- Data pasar (minimal satu provider) ---${NC}"
    echo -e "${CYAN}OANDA (FX/metals — disarankan). Buat akun practice di oanda.com → Manage API Access.${NC}"
    read -rp "$(echo -e "${CYAN}OANDA API Token (opsional):${NC} ")" OANDA_TOKEN
    read -rp "$(echo -e "${CYAN}OANDA Account ID (opsional):${NC} ")" OANDA_ACCOUNT
    read -rp "$(echo -e "${CYAN}OANDA env [practice/live, default practice]:${NC} ")" OANDA_ENVIRONMENT
    OANDA_ENVIRONMENT="${OANDA_ENVIRONMENT:-practice}"
    read -rp "$(echo -e "${CYAN}TwelveData API Key (opsional):${NC} ")" TWELVEDATA_KEY
    read -rp "$(echo -e "${CYAN}Finnhub API Key (opsional):${NC} ")" FINNHUB_KEY

    # NOTE: Kredensial LLM (Gemini/OpenAI/xAI/OpenRouter/Codex/Vertex, API key & OAuth)
    # TIDAK lagi dikumpulkan di sini. Setelah container app sehat, STEP "Model Wizard"
    # menjalankan `rtrade setup wizard` — pilih banyak provider × model, API key atau
    # OAuth (device-code Codex / PKCE xAI), multi-akun, dengan fallback otomatis.
    info "Kredensial LLM diatur lewat Model Wizard setelah install (multi-provider, OAuth/API key)."

    echo ""; echo -e "${BOLD}--- Telegram ---${NC}"
    read -rp "$(echo -e "${CYAN}Telegram Bot Token:${NC} ")" TELEGRAM_TOKEN
    read -rp "$(echo -e "${CYAN}Telegram Chat ID:${NC} ")" TELEGRAM_CHAT
    echo ""; echo -e "${BOLD}--- Domain (opsional) ---${NC}"
    read -rp "$(echo -e "${CYAN}Domain (kosong = localhost):${NC} ")" DOMAIN

    info "Generating .env file..."
    {
        echo "# Auto-generated by setup_vps.sh on $(date -Iseconds) — JANGAN commit"
        echo "RTRADE_DB_PASSWORD=${DB_PASSWORD}"
        echo "DATABASE_URL=postgresql+asyncpg://rtrade:${DB_PASSWORD}@db:5432/rtrade"
        echo "REDIS_URL=redis://redis:6379/0"
        echo ""
        echo "TWELVEDATA_API_KEY=${TWELVEDATA_KEY}"
        echo "FINNHUB_API_KEY=${FINNHUB_KEY}"
        echo "OANDA_ENV=${OANDA_ENVIRONMENT}"
        echo "OANDA_TOKEN_1=${OANDA_TOKEN}"
        echo "OANDA_ACCOUNT_1=${OANDA_ACCOUNT}"
        echo ""
        echo "# === LLM ==="
        echo "# Diisi oleh Model Wizard: 'rtrade setup wizard' (multi-provider, API key/OAuth)."
        echo "# Multi-key = fallback otomatis; limit langganan ~5 jam → rotasi akun (llm.pool)."
        echo ""
        echo "# === OAuth token store (WAJIB di prod — token disimpan terenkripsi) ==="
        echo "RTRADE_TOKEN_KEY=${TOKEN_KEY}"
        echo ""
        echo "TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}"
        echo "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT}"
        echo "API_AUTH_TOKEN=${AUTH_TOKEN}"
        echo "DOMAIN=${DOMAIN:-localhost}"
        echo "ENV=prod"
        echo "LOG_LEVEL=INFO"
    } > "$INSTALL_DIR/.env"

    chmod 600 "$INSTALL_DIR/.env"
    chown "$APP_USER:$APP_USER" "$INSTALL_DIR/.env"
    success ".env file created (permissions: 600, RTRADE_TOKEN_KEY terisi)"
}

# ============================================================================
# STEP 6: BUILD & START SERVICES
# ============================================================================
build_and_start() {
    step "6/9 — Build & Start Services"
    
    cd "$INSTALL_DIR"
    
    info "Building Docker images (this may take a few minutes)..."
    sudo -u "$APP_USER" docker compose \
        -f docker-compose.yml \
        -f docker-compose.prod.yml \
        build --quiet 2>&1 | tail -5
    success "Docker images built"
    
    info "Starting all services..."
    local profiles=""
    [[ -n "${TELEGRAM_TOKEN:-}" ]] && profiles="--profile telegram"
    sudo -u "$APP_USER" docker compose \
        -f docker-compose.yml \
        -f docker-compose.prod.yml \
        $profiles up -d 2>&1 | tail -10
    success "All services started"
    
    # Wait for services to be ready
    info "Waiting for services to be healthy..."
    local max_wait=120
    local elapsed=0
    
    while [[ $elapsed -lt $max_wait ]]; do
        local healthy
        healthy=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml ps --format json 2>/dev/null \
            | jq -r '.Health // .State' 2>/dev/null \
            | grep -c "healthy\|running" || echo "0")
        
        if [[ $healthy -ge 6 ]]; then
            break
        fi
        
        sleep 5
        ((elapsed += 5))
        echo -ne "\r  Waiting... ${elapsed}s / ${max_wait}s"
    done
    echo ""
    
    if [[ $elapsed -ge $max_wait ]]; then
        warn "Timeout waiting for all services. Checking status..."
    else
        success "Services ready in ${elapsed}s"
    fi
}

# ============================================================================
# STEP 7: DATABASE MIGRATION
# ============================================================================
run_migrations() {
    step "7/9 — Database Migration"
    
    cd "$INSTALL_DIR"
    
    info "Waiting for database to be ready..."
    local retries=30
    while [[ $retries -gt 0 ]]; do
        if docker compose -f docker-compose.yml -f docker-compose.prod.yml \
            exec -T db pg_isready -U rtrade -d rtrade > /dev/null 2>&1; then
            break
        fi
        ((retries--))
        sleep 2
    done
    
    if [[ $retries -eq 0 ]]; then
        error "Database not ready after 60s!"
        return 1
    fi
    success "Database is ready"
    
    info "Running Alembic migrations..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
        exec -T app python -m alembic upgrade head 2>&1 || {
        warn "Migration failed on first try, retrying..."
        sleep 5
        docker compose -f docker-compose.yml -f docker-compose.prod.yml \
            exec -T app python -m alembic upgrade head 2>&1 || {
            warn "Migration failed again. Check alembic configuration."
        }
    }
    success "Database migration complete"

    # Verify tables
    info "Verifying database tables..."
    docker compose -f docker-compose.yml -f docker-compose.prod.yml \
        exec -T db psql -U rtrade -d rtrade -c "\dt" 2>&1 | head -30
}

# ============================================================================
# STEP 7b: MODEL WIZARD (LLM providers/models — API key & OAuth, multi-akun)
# ============================================================================
run_model_wizard() {
    step "7b — Model Wizard (LLM: provider × model, API key / OAuth)"

    cd "$INSTALL_DIR"
    local CEX_I="docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app"

    echo -e "${BOLD}Pilih provider & model AI yang dipakai bot.${NC}"
    echo "  • API key  : Gemini / Anthropic / OpenAI / xAI / OpenRouter (1 key → 300+ model)"
    echo "  • OAuth     : Codex (device-code), xAI Grok (PKCE), Google Vertex"
    echo -e "${YELLOW}  OAuth di VPS headless: pakai mode tempel (manual-paste). Untuk xAI PKCE yang${NC}"
    echo -e "${YELLOW}  butuh loopback 127.0.0.1:56121, jalankan dari laptop: ssh -N -L 56121:127.0.0.1:56121 user@vps${NC}"
    echo ""

    if confirm "Jalankan Model Wizard sekarang?"; then
        # Interactive (perlu TTY) — wizard menulis .env + config/settings.yaml.
        sudo -u "$APP_USER" $CEX_I python -m rtrade.cli.setup wizard \
            --env-file /app/.env --settings /app/config/settings.yaml || {
            warn "Wizard dibatalkan/gagal — Anda bisa ulang: make wizard (atau exec app python -m rtrade.cli.setup wizard)"
        }
        # Verifikasi pool kredensial.
        sudo -u "$APP_USER" $CEX_I python -m rtrade.cli.setup verify \
            --settings /app/config/settings.yaml || \
            warn "Pool kredensial masih kosong — bot jalan tanpa LLM sampai wizard diisi."
    else
        warn "Wizard dilewati — jalankan nanti: rtrade setup wizard (lihat docs/AUTH_OAUTH.md)."
    fi
}

# ============================================================================
# STEP 7c: BACKFILL DATA HISTORIS (otomatis, fail-soft, data-driven)
# ============================================================================
run_backfill() {
    step "7c — Backfill data historis (semua instrumen × timeframe)"

    if [[ "${SKIP_BACKFILL:-0}" == "1" ]]; then
        warn "Backfill dilewati (--skip-backfill)."
        return 0
    fi

    cd "$INSTALL_DIR"
    local CEX="docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T app"

    info "Backfill berjalan otomatis (fail-soft: simbol gagal dilewati, lanjut)..."
    sudo -u "$APP_USER" $CEX python -m rtrade.cli.backfill --all --days "${BACKFILL_DAYS:-1095}" || \
        warn "Sebagian backfill gagal — bot tetap start; ulangi nanti: rtrade backfill --all"
    success "Backfill selesai (lihat log untuk detail per simbol)."
}

# ============================================================================
# STEP 8: SETUP LOG ROTATION
# ============================================================================
setup_logrotate() {
    step "8/9 — Log Rotation & Maintenance"
    
    # Docker log rotation
    cat > /etc/docker/daemon.json << 'DAEMONJSON'
{
    "log-driver": "json-file",
    "log-opts": {
        "max-size": "50m",
        "max-file": "3"
    }
}
DAEMONJSON
    
    # Application log rotation
    cat > /etc/logrotate.d/robil-trade << LOGROTATE
${INSTALL_DIR}/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 ${APP_USER} ${APP_USER}
}
LOGROTATE
    
    success "Log rotation configured (Docker: 50MB × 3, App: 14 days)"
    
    # Restart Docker to apply log config
    systemctl restart docker > /dev/null 2>&1
    
    # Re-start services after Docker restart
    cd "$INSTALL_DIR"
    sleep 3
    local profiles=""
    [[ -n "${TELEGRAM_TOKEN:-}" ]] && profiles="--profile telegram"
    sudo -u "$APP_USER" docker compose \
        -f docker-compose.yml \
        -f docker-compose.prod.yml \
        $profiles up -d > /dev/null 2>&1
    success "Services restarted with new log config"
}

# ============================================================================
# STEP 9: VERIFY & SUMMARY
# ============================================================================
verify_and_summary() {
    step "9/9 — Verification & Summary"
    
    cd "$INSTALL_DIR"
    
    echo ""
    echo -e "${BOLD}Service Status:${NC}"
    divider
    
    # Container status
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps \
        --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || \
    docker compose -f docker-compose.yml -f docker-compose.prod.yml ps
    
    divider
    
    # Health check
    local api_health
    api_health=$(curl -sf http://localhost:8000/health 2>/dev/null || echo '{"status":"unreachable"}')
    
    echo ""
    echo -e "${BOLD}Health Check:${NC}"
    if echo "$api_health" | jq . > /dev/null 2>&1; then
        echo "$api_health" | jq .
    else
        echo "  API: $api_health"
    fi
    
    # Final summary
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}  ${GREEN}${BOLD}✅ ROBIL TRADE — SETUP COMPLETE!${NC}                            ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  📂 Install dir : ${BOLD}${INSTALL_DIR}${NC}                       ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  👤 App user    : ${BOLD}${APP_USER}${NC}                                    ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🔐 .env        : ${BOLD}${INSTALL_DIR}/.env${NC}                   ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}Endpoints:${NC}                                                  ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🌐 API         : https://\${DOMAIN} (atau http://IP:8000)     ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🤖 Telegram    : /health, /status, /calibration             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  📊 Metrics     : http://localhost:8000/metrics               ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${BOLD}Useful Commands:${NC}                                            ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  📋 Logs        : make prod-logs                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🏥 Health      : make prod-health                            ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🔄 Restart     : make prod                                   ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  💾 Backup      : make backup                                 ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  🛑 Stop        : make prod-down                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}  ${YELLOW}⚠️  NEXT STEPS:${NC}                                             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  1. Verify .env credentials are correct                      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  2. Send /health to Telegram bot                             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  3. Monitor logs: make prod-logs                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  4. Start 4-8 week paper calibration (P4-T6)                 ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  5. LLM model/provider : rtrade setup wizard (jika dilewati)  ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  6. Backfill ulang (opsional) : rtrade backfill --all         ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${RED}⚠️  DISCLAIMER: Sinyal analisis BUKAN nasihat keuangan.${NC}    ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${RED}Selalu gunakan manajemen risiko yang ketat.${NC}               ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"

    local CEX="docker compose -f docker-compose.yml -f docker-compose.prod.yml exec app"
    echo ""; echo -e "${BOLD}${YELLOW}── LLM / Model (atur kapan saja) ──${NC}"
    echo "  Tambah/ubah provider & model : $CEX python -m rtrade.cli.setup wizard"
    echo "  Cek pool kredensial          : $CEX python -m rtrade.cli.setup verify"
    echo "  Status OAuth                 : $CEX python -m rtrade.cli.auth status"
    echo -e "${YELLOW}  xAI Grok (PKCE) di VPS: dari laptop jalankan${NC}"
    echo "    ssh -N -L 56121:127.0.0.1:56121 user@vps   lalu pilih xAI di wizard (mode tempel)."
    echo "  Set RTRADE_XAI_CLIENT_ID di .env sebelum login xAI OAuth."
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================
main() {
    banner
    check_root

    # Optional flags: --skip-backfill, --backfill-days N
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-backfill) SKIP_BACKFILL=1; shift ;;
            --backfill-days) BACKFILL_DAYS="${2:-1095}"; shift 2 ;;
            *) shift ;;
        esac
    done
    
    echo -e "${BOLD}Script ini akan menginstall dan mengkonfigurasi Robil Trade${NC}"
    echo -e "${BOLD}secara otomatis di VPS ini.${NC}"
    echo ""
    
    if ! confirm "Lanjutkan instalasi?"; then
        echo "Instalasi dibatalkan."
        exit 0
    fi
    
    local start_time
    start_time=$(date +%s)
    
    check_system          # Step 1
    install_prerequisites # Step 2
    setup_security        # Step 3
    clone_repo            # Step 4
    collect_credentials   # Step 5
    build_and_start       # Step 6
    run_migrations        # Step 7
    run_model_wizard      # Step 7b — LLM providers/models (API key & OAuth)
    run_backfill          # Step 7c — historical data (auto, fail-soft)
    setup_logrotate       # Step 8
    verify_and_summary    # Step 9
    
    local elapsed=$(( $(date +%s) - start_time ))
    local minutes=$(( elapsed / 60 ))
    local seconds=$(( elapsed % 60 ))
    echo -e "${GREEN}Total setup time: ${minutes}m ${seconds}s${NC}"
    echo ""
}

main "$@"
