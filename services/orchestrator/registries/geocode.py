"""Address geocoding for registry lookups (Nominatim / OSM)."""
from __future__ import annotations

from typing import Any

import httpx

from registries.config import GEOCODE_URL, HTTP_TIMEOUT_S


def geocode_address(address: str, state: str = "") -> dict[str, Any] | None:
    query = address.strip()
    if state and state.lower() not in query.lower():
        query = f"{query}, {state}, México"
    params = {
        "q": query,
        "format": "json",
        "limit": "1",
        "countrycodes": "mx",
    }
    headers = {"User-Agent": "CasaOrquesta-Voice/0.1 (real-estate audit)"}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(GEOCODE_URL, params=params, headers=headers)
            resp.raise_for_status()
            rows = resp.json()
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    try:
        lat = float(row["lat"])
        lng = float(row["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "lat": lat,
        "lng": lng,
        "display_name": row.get("display_name", query),
        "provider": "nominatim",
    }
