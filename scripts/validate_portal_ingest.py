#!/usr/bin/env python3
"""Run colonia pilot ingest across portals and print per-portal validation."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def _load_dotenv() -> None:
    import os

    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and (val or key not in os.environ):
            os.environ[key] = val


_load_dotenv()

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ingestion.runner import run_market_ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Colonia pilot ingest with per-portal validation")
    parser.add_argument(
        "--colonia",
        action="append",
        default=["Prados Churubusco"],
        help="Colonia name(s) to scrape (repeatable)",
    )
    parser.add_argument("--no-push", action="store_true", help="Skip POST to listings service")
    parser.add_argument("--merge-cache", action="store_true", help="Union into market_listings.json")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print portal_validation block as JSON only",
    )
    args = parser.parse_args()

    result = asyncio.run(
        run_market_ingest(
            include_portal=True,
            include_easybroker=False,
            push=not args.no_push,
            merge_cache=args.merge_cache,
            colonia_pilot=True,
            colonia_names=set(args.colonia),
        )
    )

    validation = (result.get("meta") or {}).get("portal_validation") or {}
    if args.json:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0 if validation.get("status") != "fail" else 1

    print("[validate-portals] Colonia pilot complete")
    print(f"  overall status : {validation.get('status', 'unknown')}")
    for portal in validation.get("portals") or []:
        if portal.get("skipped"):
            print(f"  {portal.get('portal')}: skipped ({portal.get('reason')})")
            continue
        print(
            f"  {portal.get('portal')}: {portal.get('status')} — "
            f"{portal.get('kept_total', 0)} kept / {portal.get('raw_total', 0)} raw "
            f"({portal.get('pass_count', 0)} pass, {portal.get('warn_count', 0)} warn, "
            f"{portal.get('fail_count', 0)} fail)"
        )

    print("")
    print("Colonia comparison:")
    for row in validation.get("colonia_compare") or []:
        colonia = row.get("colonia") or row.get("query_key")
        print(f"  {colonia}:")
        for portal, stats in (row.get("portals") or {}).items():
            print(
                f"    {portal}: kept={stats.get('kept_count', 0)} "
                f"raw={stats.get('raw_count', 0)} status={stats.get('status')}"
            )

    if result.get("manifest_path"):
        print(f"\nmanifest: {result['manifest_path']}")
    return 0 if validation.get("status") != "fail" else 1


if __name__ == "__main__":
    raise SystemExit(main())
