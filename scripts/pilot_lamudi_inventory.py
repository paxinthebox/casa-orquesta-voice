#!/usr/bin/env python3
"""One-off Lamudi MX inventory pilot via thescrapelab/the-ultimate-property-scraper."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


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
        if key and key not in os.environ:
            os.environ[key] = val


def _run(client: httpx.Client, url: str, payload: dict[str, object]) -> list[dict]:
    label = str(payload.get("areaQuery") or "mx")
    print(f"[lamudi-pilot] {label} …", flush=True)
    resp = client.post(url, json=payload)
    if resp.status_code >= 400:
        print(f"[lamudi-pilot] HTTP {resp.status_code}: {resp.text[:500]}", flush=True)
        return []
    rows = resp.json()
    if not isinstance(rows, list):
        print(f"[lamudi-pilot] unexpected response: {str(rows)[:200]}", flush=True)
        return []
    print(f"[lamudi-pilot] {label}: {len(rows)} rows", flush=True)
    if rows:
        sample = rows[0]
        print(
            "[lamudi-pilot] sample:",
            {k: sample.get(k) for k in (
                "listing_id", "url", "title", "city", "suburb", "location",
                "price", "property_type", "market", "source", "country_code",
            )},
            flush=True,
        )
    return [r for r in rows if isinstance(r, dict)]


def main() -> int:
    _load_dotenv()
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        print("APIFY_API_TOKEN not set", file=sys.stderr)
        return 1

    actor = os.getenv(
        "APIFY_LAMUDI_INVENTORY_ACTOR",
        "thescrapelab/the-ultimate-property-scraper",
    ).replace("/", "~")
    timeout = int(os.getenv("APIFY_RUN_TIMEOUT_SEC", "180"))
    url = (
        f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
        f"?token={token}&timeout={timeout}"
    )

    tests: list[dict[str, object]] = [
        {
            "countries": ["MX"],
            "websites": ["lamudimxproperty"],
            "markets": ["for-sale"],
            "areaQuery": "Cuernavaca",
            "limit": 20,
        },
        {
            "countries": ["MX"],
            "websites": ["lamudimxproperty"],
            "markets": ["for-sale"],
            "areaQuery": "Coyoacan",
            "propertyTypeQuery": "house",
            "limit": 20,
        },
        {
            "countries": ["MX"],
            "websites": ["lamudimxproperty"],
            "markets": ["for-sale"],
            "areaQuery": "Prado Churubusco",
            "limit": 20,
        },
    ]

    all_rows: list[dict] = []
    with httpx.Client(timeout=timeout + 30) as client:
        for payload in tests:
            all_rows.extend(_run(client, url, payload))

    out_path = ROOT / "data" / "cache" / "lamudi_pilot.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[lamudi-pilot] wrote {len(all_rows)} rows → {out_path}", flush=True)
    return 0 if all_rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
