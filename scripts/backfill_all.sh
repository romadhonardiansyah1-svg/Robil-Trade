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
