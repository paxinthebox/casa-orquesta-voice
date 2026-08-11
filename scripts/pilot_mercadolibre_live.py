#!/usr/bin/env python3
"""Pilot Mercado Libre Inmuebles MX live scrape — local HTML parse or Apify actor."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.mercadolibre import normalize_mercadolibre_row  # noqa: E402
from ingestion.portals.mercadolibre_live_parse import (  # noqa: E402
    paginate_urls,
    parse_serp_html,
)
from ingestion.portals.mercadolibre_urls import mercadolibre_search_url  # noqa: E402

PILOT_URLS = {
    "coyoacan": {
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "departamentos",
        "transactionType": "venta",
        "scope": "borough",
    },
    "prado": {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "aliases": ["prado churubusco"],
        "scope": "colonia",
    },
    "cuernavaca": {
        "city": "Cuernavaca",
        "state": "Morelos",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "borough",
    },
}

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ[key] = val


def _fetch_local_serp(client: httpx.Client, start_url: str, max_items: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page_url in paginate_urls(start_url, max_pages=10):
        resp = client.get(
            page_url,
            headers={"User-Agent": _UA, "Accept-Language": "es-MX,es;q=0.9"},
            follow_redirects=True,
        )
        if resp.status_code >= 400 or "para continuar, ingresa" in resp.text.lower():
            print(f"[ml-live] blocked/failed {page_url} ({resp.status_code})", flush=True)
            break
        page_rows = parse_serp_html(resp.text, start_url=start_url)
        print(f"[ml-live] {page_url} → {len(page_rows)} rows", flush=True)
        if not page_rows:
            break
        added = 0
        for row in page_rows:
            item_id = str(row.get("item_id") or "")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            rows.append(row)
            added += 1
            if len(rows) >= max_items:
                return rows
        if added == 0:
            break
    return rows


async def _fetch_apify_actor(start_url: str, max_items: int) -> list[dict[str, Any]]:
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    actor = os.getenv("APIFY_MERCADOLIBRE_ACTOR", "").strip()
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not set")
    if not actor:
        raise RuntimeError(
            "APIFY_MERCADOLIBRE_ACTOR not set — deploy infra/apify/mercadolibre-inmuebles-mx-scraper first"
        )

    timeout = int(os.getenv("APIFY_MERCADOLIBRE_TIMEOUT_SEC", "360"))
    actor_path_slug = actor.replace("/", "~")
    build = os.getenv("APIFY_MERCADOLIBRE_BUILD", "").strip()
    params = f"token={token}&timeout={timeout}"
    if build:
        params += f"&build={build}"
    url = f"https://api.apify.com/v2/acts/{actor_path_slug}/run-sync-get-dataset-items?{params}"
    payload: dict[str, Any] = {
        "startUrl": start_url,
        "maxItems": max_items,
        "maxPages": int(os.getenv("APIFY_MERCADOLIBRE_MAX_PAGES", "10")),
        "proxyConfiguration": {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "MX",
        },
    }
    print(f"[ml-live] Apify {actor} …", flush=True)
    async with httpx.AsyncClient(timeout=timeout + 30.0) as client:
        resp = await client.post(url, json=payload)
    if resp.status_code >= 400:
        raise RuntimeError(f"Apify HTTP {resp.status_code}: {resp.text[:300]}")
    rows = resp.json()
    if not isinstance(rows, list):
        raise RuntimeError("unexpected Apify response")
    return [r for r in rows if isinstance(r, dict)]


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Pilot Mercado Libre Inmuebles MX live scrape")
    parser.add_argument(
        "target",
        nargs="?",
        choices=["coyoacan", "prado", "cuernavaca", "all"],
        default="coyoacan",
        help="Pilot market (default: coyoacan)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Fetch SERP directly (no Apify actor; likely blocked without MX IP)",
    )
    parser.add_argument(
        "--fixture",
        action="store_true",
        help="Parse committed HTML fixture only (offline smoke)",
    )
    parser.add_argument("--max-items", type=int, default=40)
    args = parser.parse_args()

    targets = list(PILOT_URLS) if args.target == "all" else [args.target]
    all_raw: list[dict[str, Any]] = []
    all_normalized: list[dict[str, Any]] = []

    for name in targets:
        query = PILOT_URLS[name]
        start_url = mercadolibre_search_url(query)
        print(f"[ml-live] === {name} === {start_url}", flush=True)

        if args.fixture:
            fixture = ROOT / "data" / "fixtures" / "mercadolibre_coyoacan_serp.html"
            raw_rows = parse_serp_html(fixture.read_text(encoding="utf-8"), start_url=start_url)
        elif args.local:
            with httpx.Client(timeout=60.0) as client:
                raw_rows = _fetch_local_serp(client, start_url, args.max_items)
        else:
            raw_rows = asyncio.run(_fetch_apify_actor(start_url, args.max_items))

        print(f"[ml-live] {name}: {len(raw_rows)} raw rows", flush=True)
        all_raw.extend(raw_rows)

        os.environ["INGEST_MERCADOLIBRE_LIVE"] = "1"
        for raw in raw_rows:
            row = normalize_mercadolibre_row(raw, query)
            if row:
                all_normalized.append(row)

    out_raw = ROOT / "data" / "cache" / "mercadolibre_live_pilot_raw.json"
    out_norm = ROOT / "data" / "cache" / "mercadolibre_live_pilot.json"
    out_raw.parent.mkdir(parents=True, exist_ok=True)
    out_raw.write_text(json.dumps(all_raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_norm.write_text(
        json.dumps(all_normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"[ml-live] normalized {len(all_normalized)} → {out_norm}", flush=True)
    if all_normalized:
        sample = all_normalized[0]
        print(
            "[ml-live] first:",
            {
                "id": sample.get("id"),
                "title": (sample.get("title") or "")[:60],
                "price_mxn": sample.get("price_mxn"),
                "neighborhood": sample.get("neighborhood"),
                "city": sample.get("city"),
                "state": sample.get("state"),
            },
            flush=True,
        )
    return 0 if all_normalized else 2


if __name__ == "__main__":
    raise SystemExit(main())
