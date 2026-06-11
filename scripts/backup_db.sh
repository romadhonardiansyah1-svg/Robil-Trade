#!/bin/sh
# ============================================================================
# Robil Trade — Database Backup Script
# Runs daily via cron (3:00 AM UTC) in the backup container.
# Ref: IMPLEMENTATION_PLAN P4-T3
#
# Features:
# - pg_dump with compression
# - 30-day retention (auto-prune old backups)
# - Exit code logging for alerting
# ============================================================================

set -e

BACKUP_DIR="/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/rtrade_${TIMESTAMP}.sql.gz"
RETENTION_DAYS=30

# Ensure backup directory exists.
mkdir -p "${BACKUP_DIR}"

echo "[$(date -Iseconds)] Starting database backup..."

# Perform pg_dump with compression.
pg_dump \
    -h db \
    -U rtrade \
    -d rtrade \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    | gzip > "${BACKUP_FILE}"

# Verify backup is non-empty.
BACKUP_SIZE=$(stat -f%z "${BACKUP_FILE}" 2>/dev/null || stat -c%s "${BACKUP_FILE}" 2>/dev/null)
if [ "${BACKUP_SIZE}" -lt 1000 ]; then
    echo "[$(date -Iseconds)] ERROR: Backup file too small (${BACKUP_SIZE} bytes). Possible failure."
    exit 1
fi

echo "[$(date -Iseconds)] Backup created: ${BACKUP_FILE} (${BACKUP_SIZE} bytes)"

# Prune old backups (older than RETENTION_DAYS).
PRUNED=$(find "${BACKUP_DIR}" -name "rtrade_*.sql.gz" -mtime +${RETENTION_DAYS} -print -delete | wc -l)
echo "[$(date -Iseconds)] Pruned ${PRUNED} old backups (> ${RETENTION_DAYS} days)"

# List current backups.
echo "[$(date -Iseconds)] Current backups:"
ls -lh "${BACKUP_DIR}"/rtrade_*.sql.gz 2>/dev/null || echo "  (none)"

echo "[$(date -Iseconds)] Backup complete."
