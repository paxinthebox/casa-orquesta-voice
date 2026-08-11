"""Nearby listing queries — Phase 6."""
from __future__ import annotations

import os
import sys
from typing import Any

_SHARED_CANDIDATES = (
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared")),
    "/_shared",
)
for _path in _SHARED_CANDIDATES:
    if os.path.isdir(_path) and _path not in sys.path:
        sys.path.insert(0, _path)
        break

from geo import haversine_km, listing_coords  # noqa: E402


def filter_nearby(
    rows: list[dict[str, Any]],
    *,
    lat: float,
    lng: float,
    radius_km: float,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        coords = listing_coords(row)
        if coords is None:
            continue
        d_km = haversine_km(lat, lng, coords[0], coords[1])
        if d_km <= radius_km:
            scored.append((d_km, row))
    scored.sort(key=lambda t: t[0])
    out: list[dict[str, Any]] = []
    for d_km, row in scored[:limit]:
        item = dict(row)
        item["distance_km"] = round(d_km, 3)
        out.append(item)
    return out


def geo_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {"total": 0, "with_coords": 0, "pct": 0.0}
    with_coords = sum(1 for r in rows if listing_coords(r) is not None)
    return {
        "total": total,
        "with_coords": with_coords,
        "missing_coords": total - with_coords,
        "pct": round(100.0 * with_coords / total, 1),
    }
