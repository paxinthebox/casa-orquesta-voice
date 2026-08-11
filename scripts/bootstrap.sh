#!/usr/bin/env bash
# Casa·Orquesta · Voice — idempotent dev environment bootstrap.
# Safe to run multiple times. Installs Python deps per service and mobile deps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'
step() { echo -e "\n${YELLOW}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }

select_python() {
    if [ -n "${PYTHON:-}" ]; then
        if "$PYTHON" - <<'PY'
import sys
raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)
PY
        then
            return
        fi
        echo "Unsupported PYTHON interpreter: $PYTHON. Use Python 3.11, 3.12, or 3.13."
        exit 1
    fi

    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'PY'
import sys
raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)
PY
        then
            PYTHON="$candidate"
            return
        fi
    done

    echo "No supported Python found. Install Python 3.11, 3.12, or 3.13, or set PYTHON=/path/to/python."
    exit 1
}

# ---------- .env ----------
if [ ! -f .env ]; then
    cp .env.example .env
    ok ".env created from .env.example — edit it with your API keys"
else
    ok ".env already exists"
fi

# ---------- Python ----------
step "Python: installing per-service deps"
select_python
ok "Using $("${PYTHON}" -c 'import sys; print(sys.executable + " (" + sys.version.split()[0] + ")")')"

for svc in orchestrator voice-gateway listings matching scheduling documents payments comms identity; do
    if [ -f "services/$svc/requirements.txt" ]; then
        echo "  → $svc"
        "$PYTHON" -m pip install -r "services/$svc/requirements.txt" --quiet \
            || echo "  (skipped — some deps may not be installable in this env)"
    fi
done

step "Python: dev tools"
"$PYTHON" -m pip install --quiet ruff mypy pytest pytest-asyncio || echo "  (skipped)"
ok "Python deps installed"

# ---------- Mobile ----------
if [ -d apps/mobile ] && [ -f apps/mobile/package.json ]; then
    step "Mobile: npm install"
    (cd apps/mobile && npm install --silent) || echo "  (skipped — node not available)"
    ok "Mobile deps installed (or skipped)"
fi

# ---------- Pre-commit ----------
if [ -f .githooks/pre-commit ]; then
    git config core.hooksPath .githooks
    ok "Pre-commit hook configured"
fi

# ---------- Verify ----------
chmod +x scripts/verify.sh scripts/bootstrap.sh 2>/dev/null || true

echo -e "\n${GREEN}━━━ bootstrap.sh: DONE ━━━${NC}"
echo "Next steps:"
echo "  1. Edit .env with at least ANTHROPIC_API_KEY (optional — sim mode if blank)"
echo "  2. Run ./scripts/verify.sh to confirm the green baseline"
echo "  3. Read docs/HANDOFF.md and start TASK_PROMPTS.md P1.1"
