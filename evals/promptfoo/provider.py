"""
Promptfoo provider — calls Casa·Orquesta run_orchestrator and returns JSON for assertions.

Config (promptfooconfig.yaml):
  mode: simulated | live
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "services" / "orchestrator"
EVAL_SCRIPTS = ROOT / "scripts" / "evals"

for p in (str(ORCH), str(EVAL_SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from stub_services import install as install_service_stubs  # noqa: E402

install_service_stubs()

import agents  # noqa: E402
from assertions import check_assertions  # noqa: E402


def _run(message: str, vars: dict, *, live: bool) -> dict:
    prev_key = os.environ.get("ANTHROPIC_API_KEY")
    if live:
        if not (prev_key or "").strip():
            return {"error": "ANTHROPIC_API_KEY required for live mode"}
    else:
        os.environ["ANTHROPIC_API_KEY"] = ""

    agents.ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
    agents.USE_REAL_AI = bool(agents.ANTHROPIC_KEY)

    state: dict = {}
    role = vars.get("client_role")
    if role in ("buyer", "seller"):
        state["client_role"] = role
    if vars.get("filters"):
        state["filters"] = vars["filters"]

    t0 = time.perf_counter()
    result = asyncio.run(agents.run_orchestrator(message, state=state))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    if not live and prev_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = prev_key
    elif not live:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    agents_invoked = sorted({s["agent"] for s in result["trace"] if s["kind"] == "agent_start"})
    return {
        "reply": result.get("reply", ""),
        "ai_mode": result.get("ai_mode"),
        "trace": result.get("trace") or [],
        "state": result.get("state") or {},
        "agents_invoked": agents_invoked,
        "elapsed_ms": elapsed_ms,
    }


def call_api(prompt, options, context):
    config = (options or {}).get("config") or {}
    mode = str(config.get("mode") or "simulated").lower()
    live = mode in ("live", "real", "claude")

    vars = (context or {}).get("vars") or {}
    message = str(vars.get("message") or prompt or "")

    try:
        payload = _run(message, vars, live=live)
    except Exception as exc:
        return {"error": str(exc)}

    if payload.get("error"):
        return {"error": payload["error"]}

    return {
        "output": json.dumps(payload, ensure_ascii=False),
        "latencyMs": payload.get("elapsed_ms"),
    }
