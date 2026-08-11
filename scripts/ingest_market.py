#!/usr/bin/env python3
"""
Whole-market ingest for Casa·Orquesta Voice.

Merges public portal listings (Inmuebles24 via Apify) with optional EasyBroker
inventory, dedupes, extracts brokers/collaborators from publisher metadata,
and pushes into the listings service + people index cache.

Usage:
  # Full market sync (portal + EasyBroker when keys are set)
  python scripts/ingest_market.py

  # Portal only
  python scripts/ingest_market.py --no-easybroker

  # EasyBroker only (same as ingest_public.py)
  python scripts/ingest_market.py --no-portal

Requires:
  APIFY_API_TOKEN          — https://console.apify.com (Inmuebles24 actor)
  EASYBROKER_API_KEY       — optional, your CRM/MLS inventory
  LISTINGS_URL             — default http://localhost:8002
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
            # Shell/Make exports win; .env only fills missing or empty values.
            if key not in os.environ:
                os.environ[key] = val
            elif not os.environ.get(key) and val:
                os.environ[key] = val


_load_dotenv()

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ingestion.easybroker import EasyBrokerError  # noqa: E402
from ingestion.runner import push_cached_listings, run_market_ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest whole MX market into Casa·Orquesta")
    parser.add_argument("--repair-cache", action="store_true",
                        help="Re-sanitize data/cache/market_listings.json (no Apify)")
    parser.add_argument("--push-only", action="store_true",
                        help="Push data/cache/market_listings.json without re-scraping")
    parser.add_argument("--no-portal", action="store_true", help="Skip Inmuebles24/Apify")
    parser.add_argument("--no-easybroker", action="store_true", help="Skip EasyBroker")
    parser.add_argument("--no-mls", action="store_true", help="Skip EasyBroker MLS feed")
    parser.add_argument("--no-push", action="store_true", help="Skip POST to listings service")
    parser.add_argument(
        "--merge-cache",
        action="store_true",
        help="Union scrape results into data/cache/market_listings.json (keep existing rows)",
    )
    parser.add_argument("--max-pages", type=int, default=10, help="EasyBroker max pages")
    parser.add_argument(
        "--queries",
        type=Path,
        default=ROOT / "data" / "market_queries.json",
        help="Portal search queries JSON",
    )
    parser.add_argument(
        "--colonia-pilot",
        action="store_true",
        help="Scrape only colonia queries from data/market_queries_colonias.json",
    )
    parser.add_argument(
        "--colonia",
        action="append",
        dest="colonias",
        help="With --colonia-pilot, limit to named colonia(s) (repeatable)",
    )
    args = parser.parse_args()

    if args.repair_cache:
        from ingestion.runner import load_listings_cache, write_listings_cache, write_people_index
        from ingestion.people_index import build_market_people_index

        repaired = load_listings_cache(repair=True)
        if not repaired:
            print("[ingest-market] No cache to repair.", file=sys.stderr)
            return 1
        path = write_listings_cache(repaired)
        collaborators, brokers = build_market_people_index(repaired)
        index_path = write_people_index(collaborators, brokers)
        print(f"[ingest-market] Repaired {len(repaired)} listings → {path}")
        print(f"[ingest-market] People index → {index_path} "
              f"({len(collaborators)} collaborators, {len(brokers)} brokers)")
        if not args.push_only:
            print("Run make push-market or make market-sync to load them into the app.")
            return 0

    if args.push_only:
        try:
            result = asyncio.run(push_cached_listings())
        except RuntimeError as exc:
            print(f"[ingest-market] {exc}", file=sys.stderr)
            print("[ingest-market] Arranca el backend: make dev", file=sys.stderr)
            return 1
        push = result.get("listings_push") or {}
        print("[ingest-market] Push from cache complete")
        print(f"  cached listings : {result.get('cached_count', 0)}")
        print(f"  pushed to API   : {push.get('ok', 0)} ok, {push.get('failed', 0)} failed")
        if push.get("first_error"):
            print(f"  first error     : {push['first_error']}", file=sys.stderr)
        if push.get("aborted"):
            print(
                f"  aborted early   : {push.get('attempted', 0)}/{result.get('cached_count', 0)} "
                "intentos (evita 9k POSTs cuando el API está caído)",
                file=sys.stderr,
            )
        if push.get("ok", 0) == 0 and result.get("cached_count", 0) > 0:
            print("[ingest-market] Ningún listing llegó al servicio — ¿make dev activo?", file=sys.stderr)
            return 1
        print("")
        print("Restart orchestrator + listings, reload the app, then search by voice.")
        return 0

    if args.no_portal and args.no_easybroker:
        print("[ingest-market] Enable at least one source (--no-portal or --no-easybroker)", file=sys.stderr)
        return 1

    sources = []
    if not args.no_portal:
        sources.append("portals (Inmuebles24 + Vivanuncios when enabled)")
    if not args.no_easybroker:
        sources.append("EasyBroker")
    mode = "colonia pilot" if args.colonia_pilot else "whole market"
    print(
        f"[ingest-market] {mode} sync starting "
        f"({', '.join(sources)}; queries={args.queries.name}) …",
        flush=True,
    )

    try:
        result = asyncio.run(
            run_market_ingest(
                include_portal=not args.no_portal,
                include_easybroker=not args.no_easybroker,
                include_mls=not args.no_mls,
                max_pages=args.max_pages,
                push=not args.no_push,
                queries_path=args.queries,
                merge_cache=args.merge_cache,
                colonia_pilot=args.colonia_pilot,
                colonia_names=set(args.colonias) if args.colonias else None,
            )
        )
    except EasyBrokerError as exc:
        print(f"[ingest-market] {exc}", file=sys.stderr)
        return 1

    meta = result.get("meta", {})
    print("[ingest-market] Whole-market sync complete")
    print(f"  listings (raw)   : {meta.get('listings_before_dedupe', 0)}")
    print(f"  listings (dedupe): {meta.get('listings_after_dedupe', 0)}")
    print(f"  collaborators    : {meta.get('collaborator_count', 0)}")
    print(f"  brokers          : {meta.get('broker_count', 0)}")

    sources = meta.get("sources") or {}
    validation = meta.get("portal_validation") or {}
    if validation:
        print(f"  portal validation: {validation.get('status', 'unknown')}")
        for portal in validation.get("portals") or []:
            if portal.get("skipped"):
                print(f"    - {portal.get('portal')}: skipped ({portal.get('reason')})")
            else:
                print(
                    f"    - {portal.get('portal')}: {portal.get('status')} — "
                    f"{portal.get('kept_total', 0)} kept / {portal.get('raw_total', 0)} raw"
                )

    for source_name in (
        "inmuebles24",
        "vivanuncios",
        "propiedades",
        "lamudi",
        "mercadolibre",
    ):
        block = sources.get(source_name) or {}
        if block.get("skipped"):
            print(f"  {source_name:14}: skipped ({block.get('reason')})")
        elif block:
            print(f"  {source_name:14}: {block.get('listing_count', 0)} listings")
            for q in block.get("queries") or []:
                err = q.get("error")
                colonia = q.get("colonia")
                label = (
                    f"{colonia} / {q.get('propertyType')} / {q.get('transactionType')}"
                    if colonia
                    else f"{q.get('city')} / {q.get('propertyType')} / {q.get('transactionType')}"
                )
                val = q.get("validation") if isinstance(q.get("validation"), dict) else {}
                val_status = val.get("status")
                if err:
                    print(f"    - {label}: error — {err}")
                else:
                    raw_n = q.get("raw_count")
                    suffix = f" ({raw_n} raw)" if raw_n not in (None, q.get("count")) else ""
                    val_suffix = f", validation={val_status}" if val_status else ""
                    print(f"    - {label}: {q.get('count', 0)}{suffix}{val_suffix}")

    eb = sources.get("easybroker") or {}
    if eb.get("skipped"):
        print(f"  easybroker       : skipped ({eb.get('reason')})")
    elif eb:
        print(f"  easybroker       : {eb.get('listing_count', 0)} published")
        if eb.get("mls_skipped"):
            print("    note: MLS feed skipped (API MLS plan required for network inventory)")

    if result.get("listings_push"):
        push = result["listings_push"]
        print(f"  pushed to API    : {push.get('ok', 0)} ok, {push.get('failed', 0)} failed")
        if push.get("first_error"):
            print(f"  push error       : {push['first_error']}", file=sys.stderr)
        if push.get("ok", 0) == 0 and meta.get("listings_after_dedupe", 0) > 0:
            print("[ingest-market] Scraped OK pero 0 pushed — corre make dev y luego make push-market",
                  file=sys.stderr)
    if result.get("listings_cache"):
        print(f"  listings cache   : {result['listings_cache']}")
    if result.get("people_index"):
        print(f"  people index     : {result['people_index']}")
    if result.get("manifest_path"):
        print(f"  manifest         : {result['manifest_path']}")

    print("")
    print("Restart orchestrator + listings, reload the app, then search by voice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
