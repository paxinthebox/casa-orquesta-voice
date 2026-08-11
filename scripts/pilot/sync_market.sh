#!/usr/bin/env bash
# Push ingested market cache to listings + restart catalog consumers.
# Run after `make ingest-market` completes (or with --push-only if cache is fresh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env" 2>/dev/null || true
  set +a
fi
LISTINGS_URL="${LISTINGS_URL:-http://localhost:8002}"

PUSH_ONLY=0
SKIP_RESTART=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --push-only) PUSH_ONLY=1; shift ;;
    --no-restart) SKIP_RESTART=1; shift ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
CACHE="$ROOT/data/cache/market_listings.json"

if [[ "$PUSH_ONLY" -eq 0 ]]; then
  if pgrep -f "ingest_market.py" >/dev/null 2>&1; then
    echo "⚠️  ingest_market.py is still running. Re-run with:"
    echo "    make market-sync   (after ingest finishes)"
    echo "    make market-sync PUSH_ONLY=1   (push existing cache now)"
    exit 1
  fi
  if [[ ! -f "$CACHE" ]]; then
    echo "No cache at $CACHE — run: make ingest-market"
    exit 1
  fi
fi

echo "━━━ Pushing market catalog ━━━"
if ! curl -sf "${LISTINGS_URL}/health" >/dev/null; then
  echo "Listings API not reachable at ${LISTINGS_URL}" >&2
  echo "Start the stack first: make dev" >&2
  exit 1
fi
"$PYTHON" scripts/ingest_market.py --push-only

if [[ "$SKIP_RESTART" -eq 0 ]]; then
  if command -v docker >/dev/null 2>&1 && docker compose ps -q listings >/dev/null 2>&1; then
    echo "━━━ Restarting catalog services ━━━"
    docker compose restart listings matching orchestrator
    echo "✓ listings, matching, orchestrator restarted"
  else
    echo "(docker compose not running — skip restart; run manually if needed)"
  fi
fi

echo "✓ market sync complete"
