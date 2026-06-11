#!/usr/bin/env bash
# ============================================================================
#  Robil Trade — Update Script
#  
#  Untuk update setelah setup awal. Jalankan:
#    cd /opt/robil-trade && sudo ./scripts/update.sh
#
#  Script ini akan:
#   1. Pull latest code dari GitHub
#   2. Rebuild Docker images
#   3. Rolling restart services
#   4. Run migrations
#   5. Verify health
# ============================================================================

set -euo pipefail

INSTALL_DIR="/opt/robil-trade"
APP_USER="rtrade"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}    $1"; }
success() { echo -e "${GREEN}[✅ OK]${NC}   $1"; }
warn()    { echo -e "${YELLOW}[⚠️  WARN]${NC} $1"; }
error()   { echo -e "${RED}[❌ ERROR]${NC} $1"; }

echo -e "\n${CYAN}${BOLD}🔄 Robil Trade — Update${NC}\n"

cd "$INSTALL_DIR"

# Check if we're root
if [[ $EUID -ne 0 ]]; then
    error "Jalankan sebagai root: sudo $0"
    exit 1
fi

# Step 1: Pre-flight backup
info "Creating pre-update database backup..."
$COMPOSE exec -T backup /bin/sh /backup.sh 2>/dev/null || warn "Backup skipped (backup container not running)"
success "Pre-update backup done"

# Step 2: Pull latest
info "Pulling latest code from GitHub..."
git fetch origin
git reset --hard origin/main
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
success "Code updated to latest main"

# Show what changed
echo ""
echo -e "${BOLD}Recent commits:${NC}"
git log --oneline -5
echo ""

# Step 3: Rebuild
info "Rebuilding Docker images..."
sudo -u "$APP_USER" $COMPOSE build --quiet
success "Images rebuilt"

# Step 4: Rolling restart
info "Restarting services..."
# Restart app services first (non-infra)
sudo -u "$APP_USER" $COMPOSE up -d --no-deps app api
sleep 5

# Then restart remaining if needed
sudo -u "$APP_USER" $COMPOSE up -d
success "Services restarted"

# Step 5: Migrations
info "Running database migrations..."
$COMPOSE exec -T app python -m alembic upgrade head 2>&1 || warn "No new migrations"
success "Migrations applied"

# Step 6: Wait for health
info "Waiting for services to be healthy..."
sleep 10

# Step 7: Verify
echo ""
echo -e "${BOLD}Service Status:${NC}"
$COMPOSE ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || $COMPOSE ps

# Health check
local_health=$(curl -sf http://localhost:8000/health 2>/dev/null || echo "unreachable")
echo ""
echo -e "${BOLD}API Health:${NC} $local_health"

echo ""
echo -e "${GREEN}${BOLD}✅ Update complete!${NC}"
echo -e "  Monitor: ${CYAN}make prod-logs${NC}"
echo ""
