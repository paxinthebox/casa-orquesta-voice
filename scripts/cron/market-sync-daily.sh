#!/usr/bin/env bash
# Push cached market listings to the catalog (no Apify scrape).
#
# Local cron example (daily 6:00 AM):
#   0 6 * * * /path/to/casa-orquesta-voice/scripts/cron/market-sync-daily.sh \
#     >> /path/to/casa-orquesta-voice/logs/market-sync.log 2>&1
#
# Use after a recent full ingest, or when data/cache/market_listings.json is fresh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

LOCK_DIR="$ROOT/data/cache/.market-sync.lock"
CACHE="$ROOT/data/cache/market_listings.json"
mkdir -p "$ROOT/data/cache" "$ROOT/logs"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[market-sync-daily] another sync is running — skipping"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

if pgrep -f "ingest_market.py" >/dev/null 2>&1; then
  echo "[market-sync-daily] ingest_market.py still running — skipping push-only"
  exit 0
fi

if [[ ! -f "$CACHE" ]]; then
  echo "[market-sync-daily] no cache at $CACHE — run ingest-market-weekly.sh first" >&2
  exit 1
fi

MAKE="${MAKE:-make}"
echo "[market-sync-daily] $(date -Iseconds) pushing cache ($(wc -c < "$CACHE" | tr -d ' ') bytes)"

"$MAKE" market-sync PUSH_ONLY=1

echo "[market-sync-daily] $(date -Iseconds) complete"
