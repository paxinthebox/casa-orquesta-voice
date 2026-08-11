#!/usr/bin/env python3
"""
bug_bash_report — Phase 4.5 decision gate.

Parses `docs/BUG_BASH.md` and:
  1. Counts issues by (severity × status).
  2. Reports the totals to stdout (or JSON with --json).
  3. Exits non-zero if the Phase 4 ship-gate is not met:
       * any P0 still open / triaged / in_pr (must be fixed or waived)
       * any P0 wontfix without an explicit waiver row + founder sign-off
       * more than --max-open-p1 (default 3) P1 in non-fixed status
       * founder hasn't signed (sign_off.founder_signed_at empty)

The script is the only gatekeeper between "passed 48h bash" and "external
invites go out". Wire it into CI; gate ship on its exit code.

Usage:
    python3 scripts/bug_bash_report.py
    python3 scripts/bug_bash_report.py --json
    python3 scripts/bug_bash_report.py --no-gate          # report-only mode
    python3 scripts/bug_bash_report.py --max-open-p1 0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}
VALID_STATUSES = {"open", "triaged", "in_pr", "fixed", "wontfix", "dup"}
NON_RESOLVED = {"open", "triaged", "in_pr"}

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "docs" / "BUG_BASH.md"


# ---------------------------------------------------------------------------
# Front-matter parser (minimal YAML, just what we need)
# ---------------------------------------------------------------------------
def _parse_front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    block = text[4:end]

    # Tiny YAML — handle nested dicts + simple lists of dicts. Good enough
    # for our schema; we don't try to be a general parser.
    root: dict = {}
    stack: list[tuple[int, object]] = [(-1, root)]

    for raw in block.splitlines():
        # strip trailing comments + spaces, keep leading indent
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        s = line.lstrip(" ")

        # Pop the stack until the parent indent is less than this one.
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if s.startswith("- "):
            entry = s[2:]
            if ":" in entry:
                k, _, v = entry.partition(":")
                item: dict = {k.strip(): _clean_yaml_scalar(v.strip())}
                if isinstance(parent, list):
                    parent.append(item)
                stack.append((indent, item))
            else:
                if isinstance(parent, list):
                    parent.append(_clean_yaml_scalar(entry.strip()))
            continue

        if ":" in s:
            k, _, v = s.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                # nested mapping or list — peek next line to decide
                container: object = {}
                if isinstance(parent, dict):
                    parent[k] = container
                stack.append((indent, container))
            else:
                val = _clean_yaml_scalar(v)
                if isinstance(parent, dict):
                    parent[k] = val
            continue

    return root


def _clean_yaml_scalar(v: str):
    v = v.strip()
    if v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    if v.startswith("'") and v.endswith("'"):
        return v[1:-1]
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if v == "" or v == "~" or v.lower() == "null":
        return ""
    try:
        if "." in v:
            return float(v)
        return int(v)
    except ValueError:
        return v


# ---------------------------------------------------------------------------
# Issue table parser
# ---------------------------------------------------------------------------
ISSUE_TABLE_HEADER_RE = re.compile(
    r"^\|\s*id\s*\|", re.M | re.I
)


def _parse_issue_rows(text: str) -> list[dict]:
    """Find the *first* table whose header column is 'id' and parse its rows."""
    lines = text.splitlines()
    i = 0
    rows: list[dict] = []
    while i < len(lines):
        if lines[i].lstrip().startswith("|") \
                and "id" in lines[i].lower() \
                and "severity" in lines[i].lower():
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            i += 1
            if i < len(lines) and lines[i].lstrip().startswith("|"):
                # skip the markdown separator row (|---|---|…)
                i += 1
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(cells) >= len(header):
                    row = dict(zip(header, cells))
                    # Skip the comment marker row.
                    if row.get("id", "").lower() not in ("_none_", "", "—"):
                        rows.append(row)
                i += 1
            break
        i += 1
    return rows


# ---------------------------------------------------------------------------
# Validation + counting
# ---------------------------------------------------------------------------
def validate(rows: list[dict]) -> list[str]:
    errs: list[str] = []
    seen_ids: set[str] = set()
    for r in rows:
        rid = r.get("id", "")
        sev = (r.get("severity") or "").upper()
        st = (r.get("status") or "").lower()
        if not rid:
            errs.append("row missing id")
            continue
        if rid in seen_ids:
            errs.append(f"duplicate id: {rid}")
        seen_ids.add(rid)
        if sev not in VALID_SEVERITIES:
            errs.append(f"{rid}: invalid severity {sev!r}; "
                        f"want one of {sorted(VALID_SEVERITIES)}")
        if st not in VALID_STATUSES:
            errs.append(f"{rid}: invalid status {st!r}; "
                        f"want one of {sorted(VALID_STATUSES)}")
        if st == "wontfix" and not r.get("waiver_by"):
            errs.append(f"{rid}: status=wontfix requires waiver_by")
    return errs


def count_by_severity(rows: list[dict]) -> dict[str, dict[str, int]]:
    bucket: dict[str, dict[str, int]] = {
        s: {st: 0 for st in VALID_STATUSES} for s in VALID_SEVERITIES
    }
    for r in rows:
        sev = (r.get("severity") or "").upper()
        st = (r.get("status") or "").lower()
        if sev in bucket and st in bucket[sev]:
            bucket[sev][st] += 1
    return bucket


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
def evaluate_gate(
    counts: dict[str, dict[str, int]],
    rows: list[dict],
    front: dict,
    *,
    max_open_p1: int = 3,
) -> tuple[bool, list[str]]:
    blockers: list[str] = []

    # 1. P0: zero open / triaged / in_pr; wontfix only with waiver
    p0_open = sum(counts["P0"].get(s, 0) for s in NON_RESOLVED)
    if p0_open > 0:
        blockers.append(f"P0: {p0_open} unresolved (must be 0)")
    p0_wontfix_no_waiver = [
        r for r in rows
        if (r.get("severity") or "").upper() == "P0"
        and (r.get("status") or "").lower() == "wontfix"
        and not r.get("waiver_by")
    ]
    if p0_wontfix_no_waiver:
        blockers.append(
            f"P0: {len(p0_wontfix_no_waiver)} wontfix rows lack waiver_by"
        )

    # 2. P1: ≤ max_open_p1 unresolved
    p1_open = sum(counts["P1"].get(s, 0) for s in NON_RESOLVED)
    if p1_open > max_open_p1:
        blockers.append(
            f"P1: {p1_open} unresolved (max {max_open_p1})"
        )

    # 3. Founder sign-off
    so = front.get("sign_off") if isinstance(front.get("sign_off"), dict) else {}
    if not (isinstance(so, dict) and so.get("founder_signed_at")):
        blockers.append("sign_off.founder_signed_at is empty")
    if not (isinstance(so, dict) and so.get("lfpdppp_reviewed_at")):
        blockers.append("sign_off.lfpdppp_reviewed_at is empty")

    return len(blockers) == 0, blockers


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Phase 4 bug-bash gate.")
    p.add_argument("--path", type=Path, default=DEFAULT_PATH,
                   help="Path to BUG_BASH.md (default: docs/BUG_BASH.md).")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON to stdout.")
    p.add_argument("--no-gate", action="store_true",
                   help="Report only — always exit 0.")
    p.add_argument("--max-open-p1", type=int, default=3,
                   help="Maximum allowed unresolved P1 issues (default 3).")
    args = p.parse_args(argv)

    if not args.path.is_file():
        print(f"  ✗ {args.path} not found", file=sys.stderr)
        return 2

    text = args.path.read_text(encoding="utf-8")
    front = _parse_front_matter(text)
    rows = _parse_issue_rows(text)
    errs = validate(rows)
    counts = count_by_severity(rows)
    gate_ok, blockers = evaluate_gate(
        counts, rows, front, max_open_p1=args.max_open_p1,
    )

    if args.json:
        out = {
            "front": front,
            "row_count": len(rows),
            "validation_errors": errs,
            "counts": counts,
            "gate_ok": gate_ok and not errs,
            "blockers": blockers + errs,
        }
        print(json.dumps(out, indent=2, sort_keys=True, default=str))
    else:
        print()
        print("=" * 70)
        print(f"  Bug-bash report — {args.path}")
        print("=" * 70)
        for sev in ("P0", "P1", "P2", "P3"):
            c = counts[sev]
            print(f"  {sev}  open/triaged/in_pr/fixed/wontfix/dup: "
                  f"{c['open']}/{c['triaged']}/{c['in_pr']}/"
                  f"{c['fixed']}/{c['wontfix']}/{c['dup']}")
        print(f"  rows total:       {len(rows)}")
        print(f"  validation errs:  {len(errs)}")
        for e in errs:
            print(f"     - {e}")
        print()
        if gate_ok and not errs:
            print("  ✅ Phase 4 ship-gate met.")
        else:
            print("  ❌ Phase 4 ship-gate NOT met:")
            for b in blockers + errs:
                print(f"     - {b}")
    if args.no_gate:
        return 0
    return 0 if (gate_ok and not errs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
