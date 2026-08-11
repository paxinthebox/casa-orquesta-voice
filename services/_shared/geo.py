"""
Shared geo helpers — Phase 6 (maps, tours, routing).

Haversine distance and nearest-neighbor ordering. Used by routing service,
listings /nearby, and (later) orchestrator tour tools.
"""
from __future__ import annotations

import math
from typing import Any, TypedDict


class GeoPoint(TypedDict):
    id: str
    lat: float
    lng: float


class RouteLeg(TypedDict, total=False):
    from_id: str
    to_id: str
    distance_m: float
    duration_s: int
    provider: str


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in meters."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return haversine_m(lat1, lng1, lat2, lng2) / 1000.0


def _drive_duration_s(distance_m: float, *, urban_kmh: float = 28.0) -> int:
    """Rough drive-time estimate when no Directions API (CDMX/Morelos urban)."""
    if distance_m <= 0:
        return 0
    hours = (distance_m / 1000.0) / urban_kmh
    return max(60, int(hours * 3600))


def nearest_neighbor_order(
    origin_lat: float,
    origin_lng: float,
    stops: list[GeoPoint],
    *,
    return_to_origin: bool = False,
) -> tuple[list[str], list[RouteLeg], float]:
    """
    Greedy nearest-neighbor visit order.

    Returns (ordered_ids, legs, total_distance_m).
    """
    if not stops:
        return [], [], 0.0

    remaining = {s["id"]: s for s in stops}
    ordered: list[str] = []
    legs: list[RouteLeg] = []
    total_m = 0.0
    cur_lat, cur_lng = origin_lat, origin_lng
    cur_id = "__origin__"

    while remaining:
        best_id = min(
            remaining,
            key=lambda sid: haversine_m(
                cur_lat, cur_lng, remaining[sid]["lat"], remaining[sid]["lng"]
            ),
        )
        nxt = remaining.pop(best_id)
        dist_m = haversine_m(cur_lat, cur_lng, nxt["lat"], nxt["lng"])
        legs.append({
            "from_id": cur_id,
            "to_id": best_id,
            "distance_m": round(dist_m, 1),
            "duration_s": _drive_duration_s(dist_m),
            "provider": "haversine",
        })
        total_m += dist_m
        ordered.append(best_id)
        cur_lat, cur_lng = nxt["lat"], nxt["lng"]
        cur_id = best_id

    if return_to_origin and ordered:
        dist_m = haversine_m(cur_lat, cur_lng, origin_lat, origin_lng)
        legs.append({
            "from_id": cur_id,
            "to_id": "__origin__",
            "distance_m": round(dist_m, 1),
            "duration_s": _drive_duration_s(dist_m),
            "provider": "haversine",
        })
        total_m += dist_m

    return ordered, legs, total_m


def listing_coords(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        lat = row.get("lat")
        lng = row.get("lng")
        if lat is None or lng is None:
            return None
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None
