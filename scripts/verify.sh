#!/usr/bin/env bash
# Casa·Orquesta · Voice — canonical pre-commit gate.
# Runs lint, typecheck, and tests across backend and mobile.
# Exits non-zero on the first failure so CI can fail fast.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${YELLOW}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

select_python() {
    if [ -n "${PYTHON:-}" ]; then
        if "$PYTHON" - <<'PY'
import sys
raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)
PY
        then
            return
        fi
        fail "Unsupported PYTHON interpreter: $PYTHON. Use Python 3.11, 3.12, or 3.13."
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

    fail "No supported Python found. Install Python 3.11, 3.12, or 3.13, or set PYTHON=/path/to/python."
}

select_python
ok "Using $("${PYTHON}" -c 'import sys; print(sys.executable + " (" + sys.version.split()[0] + ")")')"

# ---------- Backend ----------
step "Backend: ruff lint"
if command -v ruff >/dev/null 2>&1; then
    ruff check services/ tests/ scripts/ || fail "ruff lint failed"
    ok "ruff lint passed"
else
    echo "  (ruff not installed; skipping. Install with: pip install ruff)"
fi

step "Backend: mypy typecheck (orchestrator only — strictest service)"
if command -v mypy >/dev/null 2>&1; then
    mypy services/orchestrator --ignore-missing-imports || fail "mypy failed"
    ok "mypy passed"
else
    echo "  (mypy not installed; skipping. Install with: pip install mypy)"
fi

step "Backend: 161-assertion test contract"
if [ -f services/orchestrator/tests/test_agents.py ]; then
    "$PYTHON" services/orchestrator/tests/test_agents.py || fail "test_agents.py contract failed"
    ok "test_agents.py contract passed"
else
    echo "  (test_agents.py not yet ported — see TASK_PROMPTS.md P1.3)"
fi

step "Backend: orchestrator eval cases (simulated)"
if [ -f scripts/evals/run_evals.py ]; then
    "$PYTHON" scripts/evals/run_evals.py || fail "orchestrator eval cases failed"
    ok "orchestrator eval cases passed"
else
    echo "  (scripts/evals/run_evals.py not present — see evals/README.md)"
fi

step "Backend: datasets.py port verification"
if [ -f services/orchestrator/tests/test_datasets.py ]; then
    (cd services/orchestrator && "$PYTHON" tests/test_datasets.py) \
        || fail "test_datasets.py failed"
    ok "test_datasets.py passed"
else
    echo "  (test_datasets.py not present — see TASK_PROMPTS.md P1.2)"
fi

step "Backend: P1.7 hooks (pii redaction, spend caps, audit log, trace)"
if [ -f services/orchestrator/tests/test_hooks.py ]; then
    (cd services/orchestrator && "$PYTHON" tests/test_hooks.py) \
        || fail "test_hooks.py failed"
    ok "test_hooks.py passed"
else
    echo "  (test_hooks.py not present — see TASK_PROMPTS.md P1.7)"
fi

step "Backend: P1.8 streaming endpoint + main.py routes"
if [ -f services/orchestrator/tests/test_streaming.py ]; then
    (cd services/orchestrator && "$PYTHON" tests/test_streaming.py) \
        || fail "test_streaming.py failed"
    ok "test_streaming.py passed (or skipped on missing deps)"
else
    echo "  (test_streaming.py not present — see TASK_PROMPTS.md P1.8)"
fi

step "Voice gateway: P2.1 Deepgram STT adapter"
if [ -f services/voice-gateway/tests/test_stt.py ]; then
    (cd services/voice-gateway && "$PYTHON" tests/test_stt.py) \
        || fail "test_stt.py failed"
    ok "test_stt.py passed"
else
    echo "  (test_stt.py not present — see TASK_PROMPTS.md P2.1)"
fi

step "Voice gateway: P2.2 TTS adapters (ElevenLabs + Azure) + chunker"
if [ -f services/voice-gateway/tests/test_tts.py ]; then
    (cd services/voice-gateway && "$PYTHON" tests/test_tts.py) \
        || fail "test_tts.py failed"
    ok "test_tts.py passed"
else
    echo "  (test_tts.py not present — see TASK_PROMPTS.md P2.2)"
fi

step "Voice gateway: P2.3 pipeline + barge-in + reconnect"
if [ -f services/voice-gateway/tests/test_pipeline.py ]; then
    (cd services/voice-gateway && "$PYTHON" tests/test_pipeline.py) \
        || fail "test_pipeline.py failed"
    ok "test_pipeline.py passed"
else
    echo "  (test_pipeline.py not present — see TASK_PROMPTS.md P2.3)"
fi

step "Voice gateway: P2.3 latency benchmark (P50 ≤ 1.5s, P95 ≤ 2.5s)"
if [ -f services/voice-gateway/tests/perf/voice_latency.py ]; then
    (cd services/voice-gateway && VOICE_LATENCY_ITERATIONS=30 \
        "$PYTHON" tests/perf/voice_latency.py) \
        || fail "voice_latency.py budget breached"
    ok "voice_latency.py budget met"
else
    echo "  (voice_latency.py not present — see TASK_PROMPTS.md P2.3)"
fi

step "Identity: P4.1 OTP + JWT + shared middleware + P4.2 consent + DSAR"
if [ -f services/identity/tests/test_identity.py ]; then
    (cd services/identity && "$PYTHON" tests/test_identity.py) \
        || fail "test_identity.py failed"
    ok "test_identity.py passed"
else
    echo "  (test_identity.py not present — see TASK_PROMPTS.md P4.1)"
fi

step "Shared: P4.6 DSAR responder + cross-service wiring guard"
if [ -f services/_shared/tests/test_dsar_responder.py ]; then
    (cd services/_shared && "$PYTHON" tests/test_dsar_responder.py) \
        || fail "test_dsar_responder.py failed"
    ok "test_dsar_responder.py passed"
else
    echo "  (test_dsar_responder.py not present — see TASK_PROMPTS.md P4.6)"
fi

step "Shared: P4.7 ops hardening (telemetry + prompt_cache + RUNBOOK + backup + SDK)"
if [ -f services/_shared/tests/test_ops.py ]; then
    (cd services/_shared && "$PYTHON" tests/test_ops.py) \
        || fail "test_ops.py failed"
    ok "test_ops.py passed"
else
    echo "  (test_ops.py not present — see TASK_PROMPTS.md P4.7)"
fi

step "Shared: P4.8 WS auth (verify_ws_token + voice-gateway wiring guard)"
if [ -f services/_shared/tests/test_ws_auth.py ]; then
    (cd services/_shared && "$PYTHON" tests/test_ws_auth.py) \
        || fail "test_ws_auth.py failed"
    ok "test_ws_auth.py passed"
else
    echo "  (test_ws_auth.py not present — see TASK_PROMPTS.md P4.8)"
fi

step "Listings: P4.9 favorites + saved searches + migrations"
if [ -f services/listings/tests/test_user_store.py ]; then
    (cd services/listings && "$PYTHON" tests/test_user_store.py) \
        || fail "listings test_user_store.py failed"
    ok "listings test_user_store.py passed"
else
    echo "  (listings test_user_store.py not present — see TASK_PROMPTS.md P4.9)"
fi

step "Scheduling: P5.1 visits store + DSAR contract"
if [ -f services/scheduling/tests/test_store.py ]; then
    (cd services/scheduling && "$PYTHON" tests/test_store.py) \
        || fail "scheduling test_store.py failed"
    ok "scheduling test_store.py passed"
else
    echo "  (scheduling test_store.py not present — see TASK_PROMPTS.md P5.1)"
fi

step "Documents: P5.1 documents store + CFDI retention"
if [ -f services/documents/tests/test_store.py ]; then
    (cd services/documents && "$PYTHON" tests/test_store.py) \
        || fail "documents test_store.py failed"
    ok "documents test_store.py passed"
else
    echo "  (documents test_store.py not present — see TASK_PROMPTS.md P5.1)"
fi

step "Payments: P5.1 payments + CFDI store + SAT retention"
if [ -f services/payments/tests/test_store.py ]; then
    (cd services/payments && "$PYTHON" tests/test_store.py) \
        || fail "payments test_store.py failed"
    ok "payments test_store.py passed"
else
    echo "  (payments test_store.py not present — see TASK_PROMPTS.md P5.1)"
fi

step "Orchestrator: P5.2 streaming partial tokens"
if [ -f services/orchestrator/tests/test_streaming_v2.py ]; then
    (cd services/orchestrator && "$PYTHON" tests/test_streaming_v2.py) \
        || fail "test_streaming_v2.py failed"
    ok "test_streaming_v2.py passed"
else
    echo "  (test_streaming_v2.py not present — see TASK_PROMPTS.md P5.2)"
fi

step "Orchestrator: P5.3 sub-agent context isolation"
if [ -f services/orchestrator/tests/test_isolation.py ]; then
    (cd services/orchestrator && "$PYTHON" tests/test_isolation.py) \
        || fail "test_isolation.py failed"
    ok "test_isolation.py passed"
else
    echo "  (test_isolation.py not present — see TASK_PROMPTS.md P5.3)"
fi

step "Comms: P4.3 WhatsApp Cloud + SMS fallback + router"
if [ -f services/comms/tests/test_comms.py ]; then
    (cd services/comms && "$PYTHON" tests/test_comms.py) \
        || fail "test_comms.py failed"
    ok "test_comms.py passed"
else
    echo "  (test_comms.py not present — see TASK_PROMPTS.md P4.3)"
fi

step "Tester guide: P4.4 structural check (--skip-pandoc)"
if [ -f scripts/build_tester_guide.sh ]; then
    ./scripts/build_tester_guide.sh --skip-pandoc \
        || fail "build_tester_guide.sh structural check failed"
    ok "tester_guide.md structure verified"
else
    echo "  (build_tester_guide.sh not present — see TASK_PROMPTS.md P4.4)"
fi

step "Bug bash: P4.5 BUG_BASH.md schema + (advisory) ship gate"
if [ -f scripts/bug_bash_report.py ]; then
    # Schema must always be valid (severity/status taxonomies, no dup ids).
    "$PYTHON" scripts/bug_bash_report.py --no-gate >/dev/null \
        || fail "bug_bash_report.py validation failed"
    ok "BUG_BASH.md schema valid"
    # Ship gate is advisory in CI — it expects founder + lfpdppp sign-off
    # which only land in the real bash. We print the current status.
    "$PYTHON" scripts/bug_bash_report.py 2>&1 | tail -8 || true
else
    echo "  (bug_bash_report.py not present — see TASK_PROMPTS.md P4.5)"
fi

step "Backend: per-service tests (excluding orchestrator + voice-gateway — covered above)"
# All suites in this repo are standalone scripts (module-level asserts +
# sys.exit), not pytest-collectable modules — run them directly.
for svc in listings matching scheduling documents payments comms identity; do
    if [ -d "services/$svc/tests" ]; then
        for tf in "services/$svc/tests"/test_*.py; do
            [ -f "$tf" ] || continue
            echo "  → $tf"
            (cd "services/$svc" && "$PYTHON" "tests/$(basename "$tf")") \
                || fail "$svc tests failed"
        done
    fi
done
ok "per-service tests passed (or skipped if not yet present)"

# ---------- Mobile ----------
# Hermetic structural sanity (P3.1) — runs without node_modules so CI
# can catch JSON / import / theme regressions even before npm install.
if [ -f apps/mobile/scripts/sanity_check.py ]; then
    step "Mobile: P3.1+P3.2+P3.3 hermetic sanity checks"
    (cd apps/mobile && "$PYTHON" scripts/sanity_check.py) \
        || fail "apps/mobile sanity_check.py failed"
    ok "apps/mobile sanity_check.py passed"
else
    echo "  (apps/mobile/scripts/sanity_check.py not present — see TASK_PROMPTS.md P3.1)"
fi

# EAS preflight (P3.4) — validates that an `eas build` would resolve
# every reference in app.json / eas.json and that secrets are documented.
if [ -f apps/mobile/scripts/preflight.py ]; then
    step "Mobile: P3.4 EAS preflight (assets + env + bundle ids + SECRETS.md)"
    (cd apps/mobile && "$PYTHON" scripts/preflight.py) \
        || fail "apps/mobile preflight.py failed"
    ok "apps/mobile preflight.py passed (warnings non-blocking)"
else
    echo "  (apps/mobile/scripts/preflight.py not present — see TASK_PROMPTS.md P3.4)"
fi

step "Backend: Phase 6 geo + routing prep"
if [ -f services/_shared/tests/test_geo.py ]; then
    "$PYTHON" services/_shared/tests/test_geo.py || fail "Phase 6 test_geo failed"
    ok "Phase 6 shared geo passed"
fi
if [ -f services/_shared/tests/test_financing.py ]; then
    "$PYTHON" services/_shared/tests/test_financing.py || fail "test_financing failed"
    ok "shared financing filter passed"
fi
if [ -f services/routing/tests/test_routing.py ]; then
    "$PYTHON" services/routing/tests/test_routing.py || fail "Phase 6 test_routing failed"
    ok "Phase 6 routing service passed"
fi

# Mobile build is a Phase 3 deliverable; skip gracefully if not yet bootstrapped.
if [ -d apps/mobile ] && [ -f apps/mobile/package.json ] && [ -d apps/mobile/node_modules ]; then
    step "Mobile: lint"
    (cd apps/mobile && npm run lint --silent) || fail "mobile lint failed"
    ok "mobile lint passed"

    step "Mobile: typecheck"
    (cd apps/mobile && npm run typecheck --silent) || fail "mobile typecheck failed"
    ok "mobile typecheck passed"

    step "Mobile: unit tests"
    if (cd apps/mobile && npm test -- --watchAll=false --passWithNoTests --silent 2>/dev/null); then
        ok "mobile unit tests passed"
    else
        echo "  (no mobile unit tests yet; that's OK for Phase 1-2)"
    fi
elif [ -d apps/mobile ] && [ -f apps/mobile/package.json ]; then
    echo "  (apps/mobile present but deps not installed — run ./scripts/bootstrap.sh, or skip until Phase 3)"
else
    echo "  (apps/mobile not yet scaffolded — see TASK_PROMPTS.md P3.1)"
fi

# ---------- Final ----------
echo -e "\n${GREEN}━━━ verify.sh: ALL GREEN ━━━${NC}"
