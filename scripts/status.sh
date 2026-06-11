#!/usr/bin/env bash
# ============================================================================
#  Robil Trade — Status & Diagnostics
#
#  Jalankan: cd /opt/robil-trade && ./scripts/status.sh
#  
#  Menampilkan:
#   - Container status
#   - Resource usage (CPU/RAM/Disk)
#   - API health
#   - Database stats
#   - Backup status
#   - Recent logs
# ============================================================================

set -euo pipefail

INSTALL_DIR="/opt/robil-trade"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

divider() { echo -e "${CYAN}──────────────────────────────────────────────────────────────${NC}"; }

cd "$INSTALL_DIR"

echo ""
echo -e "${CYAN}${BOLD}📊 Robil Trade — System Status${NC}"
echo -e "${CYAN}   $(date -Iseconds)${NC}"

# 1. Container Status
echo ""
echo -e "${BOLD}🐳 Container Status${NC}"
divider
$COMPOSE ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || $COMPOSE ps
divider

# 2. Resource Usage
echo ""
echo -e "${BOLD}📈 Resource Usage${NC}"
divider
echo -e "${BOLD}Container          CPU%     MEM USAGE / LIMIT     MEM%${NC}"
docker stats --no-stream --format "{{.Name}}  {{.CPUPerc}}    {{.MemUsage}}    {{.MemPerc}}" 2>/dev/null \
    | grep robil || echo "  (no containers running)"
divider

# 3. System Resources
echo ""
echo -e "${BOLD}💻 System Resources${NC}"
divider
echo "  CPU:  $(nproc) cores, Load: $(cat /proc/loadavg | awk '{print $1, $2, $3}')"
echo "  RAM:  $(free -h | awk '/^Mem:/{print $3 "/" $2}')"
echo "  Disk: $(df -h / | awk 'NR==2{print $3 "/" $2 " (" $5 " used)"}')"
echo "  Swap: $(free -h | awk '/^Swap:/{print $3 "/" $2}')"
divider

# 4. API Health
echo ""
echo -e "${BOLD}🏥 API Health${NC}"
divider
api_health=$(curl -sf http://localhost:8000/health 2>/dev/null || echo '{"status":"unreachable"}')
if command -v jq &> /dev/null; then
    echo "$api_health" | jq . 2>/dev/null || echo "  $api_health"
else
    echo "  $api_health"
fi
divider

# 5. Database Stats
echo ""
echo -e "${BOLD}🗄️  Database Stats${NC}"
divider
$COMPOSE exec -T db psql -U rtrade -d rtrade -t -c "
    SELECT 'Tables: ' || COUNT(*)::text FROM information_schema.tables WHERE table_schema = 'public';
" 2>/dev/null || echo "  (database unreachable)"

$COMPOSE exec -T db psql -U rtrade -d rtrade -t -c "
    SELECT 'DB Size: ' || pg_size_pretty(pg_database_size('rtrade'));
" 2>/dev/null || true

$COMPOSE exec -T db psql -U rtrade -d rtrade -t -c "
    SELECT 'Signals: ' || COUNT(*)::text FROM signals;
" 2>/dev/null || true
divider

# 6. Backup Status
echo ""
echo -e "${BOLD}💾 Backup Status${NC}"
divider
$COMPOSE exec -T backup ls -lht /backups/ 2>/dev/null | head -5 || echo "  (no backups found)"
divider

# 7. Recent Errors
echo ""
echo -e "${BOLD}⚠️  Recent Errors (last 20 lines)${NC}"
divider
$COMPOSE logs --tail=100 app 2>/dev/null | grep -i "error\|exception\|critical" | tail -20 || echo "  (no recent errors)"
divider

echo ""
echo -e "${GREEN}Status check complete.${NC}"
echo ""
