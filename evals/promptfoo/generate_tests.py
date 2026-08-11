"""Generate promptfoo tests from evals/cases/orchestrator.json (single source of truth)."""
from __future__ import annotations

import json
from pathlib import Path


def generate_tests():
    cases_path = Path(__file__).resolve().parents[1] / "cases" / "orchestrator.json"
    with cases_path.open(encoding="utf-8") as f:
        data = json.load(f)

    tests = []
    for case in data.get("cases") or []:
        vars = dict(case.get("vars") or {})
        vars["_case_id"] = case.get("id")
        vars["_assert"] = case.get("assert") or {}

        tests.append({
            "description": f"{case.get('id')}: {case.get('description', '')}".strip(),
            "vars": vars,
            "assert": [
                {
                    "type": "python",
                    "value": "file://assert_case.py:check_output",
                },
            ],
        })
    return tests
