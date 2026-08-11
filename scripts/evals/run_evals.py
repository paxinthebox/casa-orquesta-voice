#!/usr/bin/env python3
"""
Casa·Orquesta orchestrator eval runner.

Runs declarative cases from evals/cases/orchestrator.json against the harness.
Default mode is simulated (no API key). Use --live for real Claude evals.

Usage:
  python3 scripts/evals/run_evals.py              # sim mode (CI-safe)
  python3 scripts/evals/run_evals.py --live       # requires ANTHROPIC_API_KEY
  python3 scripts/evals/run_evals.py --filter route-
  python3 scripts/evals/run_evals.py --json       # machine-readable report
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
ORCH = ROOT / "services" / "orchestrator"
CASES_FILE = ROOT / "evals" / "cases" / "orchestrator.json"

sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(ORCH))

from stub_services import install as install_service_stubs  # noqa: E402

from assertions import check_assertions  # noqa: E402


def _load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("cases") or [])


def _build_state(vars: dict) -> dict:
    state: dict = {}
    if vars.get("client_role") in ("buyer", "seller"):
        state["client_role"] = vars["client_role"]
    if vars.get("filters"):
        state["filters"] = vars["filters"]
    if vars.get("focus_listing_id"):
        state["focus_listing_id"] = vars["focus_listing_id"]
    if vars.get("focus_document_id"):
        state["focus_document_id"] = vars["focus_document_id"]
    if vars.get("focus_person_id"):
        state["focus_person_id"] = vars["focus_person_id"]
    if vars.get("focus_person_name"):
        state["focus_person_name"] = vars["focus_person_name"]
    if vars.get("focus_person_kind"):
        state["focus_person_kind"] = vars["focus_person_kind"]
    return state


async def _run_case(case: dict, *, live: bool) -> dict:
    import agents  # noqa: WPS433 — imported after stub_services.install()

    prev_key = os.environ.get("ANTHROPIC_API_KEY")
    if live:
        if not (prev_key or "").strip():
            raise RuntimeError("ANTHROPIC_API_KEY required for --live evals")
    else:
        os.environ["ANTHROPIC_API_KEY"] = ""

    # Force reload of USE_REAL_AI flag when toggling modes in-process.
    agents.ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
    agents.USE_REAL_AI = bool(agents.ANTHROPIC_KEY)

    vars = case.get("vars") or {}
    message = str(vars.get("message") or "")
    state = _build_state(vars)

    t0 = time.perf_counter()
    result = await agents.run_orchestrator(message, state=state)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    if not live and prev_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = prev_key
    elif not live:
        os.environ.pop("ANTHROPIC_API_KEY", None)

    agents_invoked = sorted({s["agent"] for s in result["trace"] if s["kind"] == "agent_start"})
    return {
        "id": case.get("id"),
        "description": case.get("description"),
        "reply": result.get("reply", ""),
        "ai_mode": result.get("ai_mode"),
        "trace": result.get("trace") or [],
        "state": result.get("state") or {},
        "agents_invoked": agents_invoked,
        "elapsed_ms": elapsed_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Casa·Orquesta orchestrator eval cases")
    parser.add_argument("--cases", type=Path, default=CASES_FILE, help="Path to cases JSON")
    parser.add_argument("--live", action="store_true", help="Use real Claude (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--filter", default="", help="Only run case ids containing this substring")
    parser.add_argument("--json", action="store_true", help="Emit JSON report to stdout")
    args = parser.parse_args()

    if not args.cases.is_file():
        print(f"Cases file not found: {args.cases}", file=sys.stderr)
        return 2

    if args.live:
        os.environ["EVAL_STUB_SERVICES"] = "0"
    else:
        install_service_stubs(force=True)

    cases = _load_cases(args.cases)
    if args.filter:
        cases = [c for c in cases if args.filter in str(c.get("id", ""))]

    if not cases:
        print("No cases matched.", file=sys.stderr)
        return 2

    mode = "live-claude" if args.live else "simulated"
    results: list[dict] = []
    passed = 0
    failed = 0

    for case in cases:
        case_id = case.get("id", "?")
        try:
            run_result = asyncio.run(_run_case(case, live=args.live))
        except Exception as exc:
            failed += 1
            results.append({
                "id": case_id,
                "pass": False,
                "error": str(exc),
            })
            continue

        failures = check_assertions(run_result, case.get("assert") or {})
        ok = not failures
        if ok:
            passed += 1
        else:
            failed += 1

        results.append({
            "id": case_id,
            "description": case.get("description"),
            "pass": ok,
            "failures": failures,
            "ai_mode": run_result.get("ai_mode"),
            "elapsed_ms": run_result.get("elapsed_ms"),
            "reply_preview": (run_result.get("reply") or "")[:200],
            "agents_invoked": run_result.get("agents_invoked"),
        })

    report = {
        "mode": mode,
        "cases_file": str(args.cases),
        "total": len(cases),
        "passed": passed,
        "failed": failed,
        "results": results,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'=' * 70}")
        print(f"  Orchestrator evals — {mode}")
        print(f"  Cases: {args.cases}")
        print(f"{'=' * 70}\n")
        for row in results:
            mark = "✅" if row.get("pass") else "❌"
            print(f"  {mark} {row.get('id')}")
            if row.get("description"):
                print(f"      {row['description']}")
            for msg in row.get("failures") or []:
                print(f"      → {msg}")
            if row.get("error"):
                print(f"      → ERROR: {row['error']}")
        print(f"\n  Passed: {passed}  Failed: {failed}  Total: {len(cases)}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
