#!/usr/bin/env bash
# ============================================================================
#  Robil Trade — VPS Auto Setup Script (Hermes-style)
#  
#  Jalankan di VPS Ubuntu 24.04:
#    curl -sSL https://raw.githubusercontent.com/romadhonardiansyah1-svg/Robil-Trade/main/scripts/setup_vps.sh | bash
#  
#  Atau download dulu lalu jalankan:
#    chmod +x setup_vps.sh && sudo ./setup_vps.sh
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
        curl -fsSL https://get.docker.com | sh > /dev/null 2>&1
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
# STEP 5: COLLECT CREDENTIALS
# ============================================================================
collect_credentials() {
    step "5/9 — Credentials & Configuration"
    
    echo ""
    echo -e "${BOLD}Masukkan API keys dan credentials.${NC}"
    echo -e "${YELLOW}Tekan Enter untuk skip (bisa diisi nanti di .env).${NC}"
    divider
    
    # Auto-generate secrets
    local DB_PASSWORD LITELLM_KEY AUTH_TOKEN
    DB_PASSWORD=$(openssl rand -hex 24)
    LITELLM_KEY=$(openssl rand -hex 32)
    AUTH_TOKEN=$(openssl rand -hex 32)
    
    info "Secrets auto-generated ✓"
    
    # Collect API keys
    local TWELVEDATA_KEY="" FINNHUB_KEY="" GEMINI_KEY_1="" GEMINI_KEY_2=""
    local TELEGRAM_TOKEN="" TELEGRAM_CHAT="" DOMAIN=""
    
    echo ""
    read -rp "$(echo -e "${CYAN}TwelveData API Key:${NC} ")" TWELVEDATA_KEY
    read -rp "$(echo -e "${CYAN}Finnhub API Key:${NC} ")" FINNHUB_KEY
    echo ""
    
    echo -e "${BOLD}--- LLM Keys ---${NC}"
    read -rp "$(echo -e "${CYAN}Gemini API Key (utama):${NC} ")" GEMINI_KEY_1
    read -rp "$(echo -e "${CYAN}Gemini API Key 2 (opsional):${NC} ")" GEMINI_KEY_2
    echo ""
    
    echo -e "${BOLD}--- Telegram ---${NC}"
    read -rp "$(echo -e "${CYAN}Telegram Bot Token:${NC} ")" TELEGRAM_TOKEN
    read -rp "$(echo -e "${CYAN}Telegram Chat ID:${NC} ")" TELEGRAM_CHAT
    echo ""
    
    echo -e "${BOLD}--- Domain (opsional, untuk TLS auto) ---${NC}"
    read -rp "$(echo -e "${CYAN}Domain (kosong = localhost):${NC} ")" DOMAIN
    
    # Write .env file
    info "Generating .env file..."
    cat > "$INSTALL_DIR/.env" << ENVEOF
# ============================================================================
# Robil Trade — Production Environment
# Auto-generated by setup_vps.sh on $(date -Iseconds)
# ⚠️  JANGAN commit file ini ke git!
# ============================================================================

# === Database ===
RTRADE_DB_PASSWORD=${DB_PASSWORD}
DATABASE_URL=postgresql+asyncpg://rtrade:${DB_PASSWORD}@db:5432/rtrade
REDIS_URL=redis://redis:6379/0

# === Data Providers ===
TWELVEDATA_API_KEY=${TWELVEDATA_KEY}
FINNHUB_API_KEY=${FINNHUB_KEY}

# === LLM ===
GEMINI_API_KEY_1=${GEMINI_KEY_1}
GEMINI_API_KEY_2=${GEMINI_KEY_2}
ANTHROPIC_API_KEY_1=
OPENAI_API_KEY_1=
# LITELLM_MASTER_KEY / LITELLM_BASE_URL tidak dipakai (LLM = library mode sejak F1).

# === Trading config (opsional, default aman) ===
# llm.enabled diatur via config/settings.yaml, bukan .env

# === Telegram ===
TELEGRAM_BOT_TOKEN=${TELEGRAM_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT}

# === API & Security ===
API_AUTH_TOKEN=${AUTH_TOKEN}
DOMAIN=${DOMAIN:-localhost}

# === Runtime ===
ENV=prod
LOG_LEVEL=INFO
ENVEOF
    
    chmod 600 "$INSTALL_DIR/.env"
    chown "$APP_USER:$APP_USER" "$INSTALL_DIR/.env"
    success ".env file created (permissions: 600)"
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
    echo -e "${CYAN}║${NC}  5. Backfill data : ./scripts/backfill_all.sh                 ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  6. Validasi      : docker compose ... exec app               ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}     python scripts/run_backtest.py ... --walkforward           ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${RED}⚠️  DISCLAIMER: Sinyal analisis BUKAN nasihat keuangan.${NC}    ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}  ${RED}Selalu gunakan manajemen risiko yang ketat.${NC}               ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# ============================================================================
# MAIN
# ============================================================================
main() {
    banner
    check_root
    
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
    setup_logrotate       # Step 8
    verify_and_summary    # Step 9
    
    local elapsed=$(( $(date +%s) - start_time ))
    local minutes=$(( elapsed / 60 ))
    local seconds=$(( elapsed % 60 ))
    echo -e "${GREEN}Total setup time: ${minutes}m ${seconds}s${NC}"
    echo ""
}

main "$@"
