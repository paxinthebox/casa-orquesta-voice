"""Load ingested market listings into the listings DB on startup."""
from __future__ import annotations

import json
import os
from typing import Any, Protocol


class _PutStore(Protocol):
    def put(self, lid: str, data: dict[str, Any]) -> None: ...


def market_cache_path() -> str:
    return os.getenv("MARKET_LISTINGS_CACHE", "/app/cache/market_listings.json")


def auto_load_enabled() -> bool:
    return os.getenv("MARKET_AUTO_LOAD", "1").strip().lower() not in {"0", "false", "no", "off"}


def load_market_cache(store: _PutStore) -> int:
    """Upsert portal inventory from disk so Docker restarts keep live data."""
    if not auto_load_enabled():
        return 0
    path = market_cache_path()
    if not os.path.isfile(path):
        return 0
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[listings] market cache skipped: {exc!r}")
        return 0
    if not isinstance(raw, list):
        return 0
    count = 0
    live_rent = 0
    for row in raw:
        if not isinstance(row, dict):
            continue
        lid = row.get("id")
        if not lid:
            continue
        payload = {k: v for k, v in row.items() if not str(k).startswith("_")}
        store.put(str(lid), payload)
        count += 1
        mode = str(payload.get("listing_mode") or "sale").lower()
        source = str(payload.get("source") or "")
        lid_s = str(lid)
        if mode == "rent" and (
            source == "inmuebles24"
            or lid_s.startswith(("I24-", "EB-", "EB-MLS-", "ML-", "VA-", "PROP-", "LAM-"))
        ):
            live_rent += 1
    if count:
        print(
            f"[listings] auto-loaded {count} market listings from {path}"
            + (f" ({live_rent} live rent)" if live_rent else "")
        )
    if live_rent:
        os.environ["LISTINGS_HAS_LIVE_RENT"] = "1"
    return count
