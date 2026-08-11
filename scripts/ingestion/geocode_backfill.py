#!/usr/bin/env python3
"""
Backfill lat/lng on market listings missing coordinates (Phase 6 P6.0).

Reads data/cache/market_listings.json (or --cache path), geocodes via Nominatim
with a local file cache, sets geo_source=geocoded, optionally --push to listings.

Usage:
  python scripts/ingestion/geocode_backfill.py
  python scripts/ingestion/geocode_backfill.py --dry-run --limit 20
  python scripts/ingestion/geocode_backfill.py --push
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ingestion.market_geo import listing_in_market, parse_location_string  # noqa: E402

DEFAULT_CACHE = ROOT / "data" / "cache" / "market_listings.json"
GEOCODE_CACHE_DIR = ROOT / "data" / "cache" / "geocode"
GEOCODE_URL = os.getenv(
    "GEOCODE_URL",
    "https://nominatim.openstreetmap.org/search",
).strip()
GEOCODE_DELAY_S = float(os.getenv("GEOCODE_DELAY_S", "1.1"))
LISTINGS_URL = os.getenv("LISTINGS_URL", "http://localhost:8002").rstrip("/")


def _cache_key(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:32]


def geocode_address(address: str, state: str = "") -> dict[str, Any] | None:
    query = address.strip()
    if state and state.lower() not in query.lower():
        query = f"{query}, {state}, México"

    GEOCODE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(query)
    cache_file = GEOCODE_CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "mx",
    }
    headers = {"User-Agent": "CasaOrquesta-Voice/0.1 (market geocode backfill)"}
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(GEOCODE_URL, params=params, headers=headers)
            resp.raise_for_status()
            rows = resp.json()
    except Exception as exc:
        print(f"[geocode-backfill] error: {query!r} — {exc}", file=sys.stderr)
        return None

    if not rows:
        return None

    row = rows[0]
    try:
        out = {
            "lat": float(row["lat"]),
            "lng": float(row["lon"]),
            "display_name": row.get("display_name", query),
            "provider": "nominatim",
        }
    except (KeyError, TypeError, ValueError):
        return None

    cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    time.sleep(GEOCODE_DELAY_S)
    return out


def _address_for_row(row: dict[str, Any]) -> tuple[str, str]:
    address = str(row.get("address") or "").strip()
    state = str(row.get("state") or "").strip()
    if not address:
        loc = str(row.get("location") or row.get("zone") or "")
        if loc:
            _, city, st = parse_location_string(loc)
            address = loc
            state = state or st or city
    return address, state


def backfill_rows(rows: list[dict[str, Any]], *, limit: int | None, dry_run: bool) -> dict[str, int]:
    stats = {"total": len(rows), "already": 0, "geocoded": 0, "failed": 0, "skipped_market": 0}
    pending = [r for r in rows if r.get("lat") is None or r.get("lng") is None]
    if limit is not None:
        pending = pending[:limit]

    for row in pending:
        if row.get("lat") is not None and row.get("lng") is not None:
            stats["already"] += 1
            continue
        address, state = _address_for_row(row)
        if not address:
            stats["failed"] += 1
            continue
        if not listing_in_market(row):
            stats["skipped_market"] += 1
            continue
        if dry_run:
            print(f"[dry-run] would geocode {row.get('id')}: {address}, {state}")
            stats["geocoded"] += 1
            continue
        geo = geocode_address(address, state)
        if not geo:
            stats["failed"] += 1
            print(f"[geocode-backfill] miss {row.get('id')}: {address}", file=sys.stderr)
            continue
        row["lat"] = geo["lat"]
        row["lng"] = geo["lng"]
        row["geo_source"] = "geocoded"
        row["geocode_display_name"] = geo.get("display_name")
        stats["geocoded"] += 1
        print(f"[geocode-backfill] {row.get('id')}: {geo['lat']:.5f}, {geo['lng']:.5f}")

    return stats


def push_rows(rows: list[dict[str, Any]]) -> int:
    pushed = 0
    with httpx.Client(timeout=30.0) as client:
        for row in rows:
            lid = row.get("id")
            if not lid:
                continue
            try:
                resp = client.post(
                    f"{LISTINGS_URL}/listings",
                    json={"id": lid, "data": row},
                )
                if resp.status_code < 300:
                    pushed += 1
            except Exception as exc:
                print(f"[geocode-backfill] push failed {lid}: {exc}", file=sys.stderr)
    return pushed


def main() -> int:
    parser = argparse.ArgumentParser(description="Geocode backfill for market listings")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--push", action="store_true", help="POST updated rows to listings service")
    args = parser.parse_args()

    if not args.cache.exists():
        print(f"[geocode-backfill] cache not found: {args.cache}", file=sys.stderr)
        return 1

    rows = json.loads(args.cache.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print("[geocode-backfill] cache must be a JSON array", file=sys.stderr)
        return 1

    stats = backfill_rows(rows, limit=args.limit, dry_run=args.dry_run)
    if not args.dry_run:
        args.cache.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[geocode-backfill] wrote {args.cache}")

    if args.push and not args.dry_run:
        n = push_rows(rows)
        print(f"[geocode-backfill] pushed {n} listings → {LISTINGS_URL}")

    print(f"[geocode-backfill] stats: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
