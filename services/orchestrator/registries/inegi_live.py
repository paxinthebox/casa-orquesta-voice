"""Live INEGI DENUE lookups (requires INEGI_API_TOKEN)."""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from registries.config import HTTP_TIMEOUT_S, INEGI_API_TOKEN


def _denue_count(lat: float, lng: float, condition: str, meters: int) -> int | None:
    if not INEGI_API_TOKEN:
        return None
    url = (
        "https://www.inegi.org.mx/app/api/denue/v1/consulta/Buscar/"
        f"{condition}/{lat},{lng}/{meters}/{INEGI_API_TOKEN}"
    )
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            rows = resp.json()
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    return len(rows)


def inegi_zone_stats(lat: float, lng: float) -> dict[str, Any] | None:
    if not INEGI_API_TOKEN:
        return None

    schools = _denue_count(lat, lng, "escuela", 1000) or 0
    units = _denue_count(lat, lng, "todos", 500) or 0
    digest = hashlib.sha1(f"{round(lat, 4)}:{round(lng, 4)}".encode()).hexdigest()[:8].upper()

    return {
        "ageb_id": f"DENUE-{digest}",
        "population": None,
        "median_household_income_mxn": None,
        "education_level_pct": {},
        "schools_within_1km": schools,
        "economic_units_500m": units,
        "metro_distance_m": None,
        "crime_index_2025": None,
        "verification_token": f"INEGI-LIVE-{digest}",
        "source": "live",
        "provider": "inegi_denue",
        "live_fields": ["schools_within_1km", "economic_units_500m"],
    }
