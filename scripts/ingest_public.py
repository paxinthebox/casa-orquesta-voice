#!/usr/bin/env python3
"""
Ingest public online inventory for independent agents in Mexico.

Primary source: EasyBroker (CRM + MLS used by most independent agents in MX).
  - Your listings: GET /properties
  - Collaborator MLS inventory: GET /mls_properties (requires API MLS plan)
  - Agents + agencies: GET /agents, GET /agencies

Results:
  - POST each listing to the listings service (alongside the demo seed)
  - Write data/cache/people_index.json for find_collaborator_agents / find_brokers

Usage:
  export EASYBROKER_API_KEY=...
  export LISTINGS_URL=http://localhost:8002   # default
  python scripts/ingest_public.py
  python scripts/ingest_public.py --no-push   # only refresh people index
  python scripts/ingest_public.py --max-pages 3
"""
from __future__ import annotations

import argparse
import asyncio
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
        if key:
            if val or key not in os.environ:
                os.environ[key] = val


_load_dotenv()

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ingestion.easybroker import EasyBrokerError  # noqa: E402
from ingestion.runner import run_ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest public MX listings + agents")
    parser.add_argument("--no-mls", action="store_true", help="Skip MLS collaborator feed")
    parser.add_argument("--no-push", action="store_true", help="Skip POST to listings service")
    parser.add_argument("--max-pages", type=int, default=10, help="Max API pages per resource")
    args = parser.parse_args()

    try:
        result = asyncio.run(
            run_ingest(
                include_mls=not args.no_mls,
                max_pages=args.max_pages,
                push=not args.no_push,
            )
        )
    except EasyBrokerError as exc:
        print(f"[ingest] {exc}", file=sys.stderr)
        return 1

    meta = result.get("meta", {})
    print("[ingest] EasyBroker sync complete")
    print(f"  listings fetched : {meta.get('listing_count', 0)}")
    print(f"  collaborators    : {meta.get('collaborator_count', 0)}")
    print(f"  brokers/agencies : {meta.get('broker_count', 0)}")
    if meta.get("mls_skipped"):
        print("  note: MLS feed skipped — API MLS plan required for collaborator inventory")
    if meta.get("listing_count", 0) == 0:
        total = meta.get("listing_total_raw", 0)
        if total:
            print(f"  note: {total} propiedades en EasyBroker pero ninguna publicada — publícalas en el CRM")
        else:
            print("  note: 0 propiedades — crea y publica inventario en EasyBroker")
    if result.get("listings_push"):
        push = result["listings_push"]
        print(f"  listings pushed  : {push.get('ok', 0)} ok, {push.get('failed', 0)} failed")
    if result.get("people_index"):
        print(f"  people index     : {result['people_index']}")
    print("")
    print("Restart orchestrator or reload app, then ask for propiedades / colaboradores / brokers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
