"""Promptfoo python assertion — validates orchestrator JSON output against case assert spec."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_SCRIPTS = ROOT / "scripts" / "evals"
if str(EVAL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(EVAL_SCRIPTS))

from assertions import check_assertions  # noqa: E402


def check_output(output, context):
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        return {"pass": False, "score": 0, "reason": f"invalid JSON output: {exc}"}

    vars = (context or {}).get("vars") or {}
    assert_spec = vars.get("_assert") or {}
    failures = check_assertions(
        {
            "reply": payload.get("reply", ""),
            "trace": payload.get("trace") or [],
            "state": payload.get("state") or {},
        },
        assert_spec,
    )
    if failures:
        return {"pass": False, "score": 0, "reason": "; ".join(failures)}
    return {"pass": True, "score": 1, "reason": "all assertions passed"}
