#!/usr/bin/env bash
# Pre-device pilot smoke — orchestrator eval subset + optional docker health checks.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
FAIL=0

step() { echo ""; echo "━━━ $1 ━━━"; }

step "Orchestrator evals (routing + guardrails)"
if ! "$PYTHON" scripts/evals/run_evals.py --filter guard-; then FAIL=1; fi
if ! "$PYTHON" scripts/evals/run_evals.py --filter route-; then FAIL=1; fi
if ! "$PYTHON" scripts/evals/run_evals.py --filter seller-; then FAIL=1; fi

if command -v curl >/dev/null 2>&1; then
  step "Service health (when docker compose is up)"
  services=(
    "gateway 8080 /api/orchestrator/health"
    "orchestrator 8001 /health"
    "voice-gateway 8010 /health"
    "listings 8002 /health"
    "matching 8003 /health"
    "routing 8008 /health"
  )
  for entry in "${services[@]}"; do
    read -r name port path <<< "$entry"
    if curl -sf "http://localhost:${port}${path}" >/dev/null 2>&1; then
      echo "  ✓ $name (:$port)"
    else
      echo "  ○ $name (:$port) — not reachable (start with: make dev)"
    fi
  done
fi

step "Behavior contract spot-check"
if ! "$PYTHON" services/orchestrator/tests/test_agents.py 2>&1 | grep -q "Passed:"; then
  echo "  ⚠ test_agents.py had failures — see output above"
  FAIL=1
else
  echo "  ✓ test_agents.py completed (check summary for failures)"
fi

echo ""
if [[ "$FAIL" -eq 0 ]]; then
  echo "✓ pilot voice smoke passed"
  exit 0
fi
echo "✗ pilot voice smoke failed"
exit 1
