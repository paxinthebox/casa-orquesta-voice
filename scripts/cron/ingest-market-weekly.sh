#!/usr/bin/env bash
# Weekly whole-market ingest (Inmuebles24 + optional EasyBroker) → push → restart catalog.
#
# Local cron example (Sunday 3:00 AM):
#   0 3 * * 0 /path/to/casa-orquesta-voice/scripts/cron/ingest-market-weekly.sh \
#     >> /path/to/casa-orquesta-voice/logs/ingest-market.log 2>&1
#
# Requires: make dev (or remote LISTINGS_URL), APIFY_API_TOKEN in .env
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

LOCK_DIR="$ROOT/data/cache/.ingest-market.lock"
mkdir -p "$ROOT/data/cache" "$ROOT/logs"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[ingest-market-weekly] another ingest is running — skipping"
  exit 0
fi
cleanup() { rmdir "$LOCK_DIR" 2>/dev/null || true; }
trap cleanup EXIT

PYTHON="${PYTHON:-python3}"
MAKE="${MAKE:-make}"

echo "[ingest-market-weekly] $(date -Iseconds) starting at $ROOT"

if ! command -v "$MAKE" >/dev/null 2>&1; then
  echo "[ingest-market-weekly] make not found" >&2
  exit 1
fi

"$MAKE" ingest-market
"$MAKE" market-sync

echo "[ingest-market-weekly] $(date -Iseconds) complete"
