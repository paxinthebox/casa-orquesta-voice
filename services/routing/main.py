"""
Casa·Orquesta — Routing Service (Phase 6 prep).

Optimizes multi-stop property tour order and (later) fetches drive polylines
from Mapbox / Google Directions. Pilot default: haversine nearest-neighbor.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from geo import GeoPoint, nearest_neighbor_order  # noqa: E402

ROUTING_PROVIDER = os.getenv("ROUTING_PROVIDER", "haversine").strip().lower()
LISTINGS_URL = os.getenv("LISTINGS_URL", "http://listings:8000").rstrip("/")
TOUR_MAX_STOPS = int(os.getenv("TOUR_MAX_STOPS", "6"))
TOUR_DWELL_MINUTES = int(os.getenv("TOUR_DWELL_MINUTES", "45"))
TOUR_TRAVEL_BUFFER_MINUTES = int(os.getenv("TOUR_TRAVEL_BUFFER_MINUTES", "10"))
MAPBOX_ACCESS_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN", "").strip()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

app = FastAPI(title="Casa·Orquesta Routing", version="0.1.0-phase6")


class Origin(BaseModel):
    type: Literal["user_location", "listing", "address"] = "user_location"
    lat: float
    lng: float
    label: str = ""


class OptimizeReq(BaseModel):
    origin: Origin
    listing_ids: list[str] = Field(..., min_length=1)
    return_to_origin: bool = False
    dwell_minutes: int = Field(default=TOUR_DWELL_MINUTES, ge=15, le=180)


class DirectionsReq(BaseModel):
    origin: Origin
    listing_ids: list[str] = Field(..., min_length=1)
    ordered_listing_ids: Optional[list[str]] = None
    return_to_origin: bool = False


async def _fetch_listing_coords(listing_ids: list[str]) -> dict[str, GeoPoint]:
    out: dict[str, GeoPoint] = {}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for lid in listing_ids:
            try:
                resp = await client.get(f"{LISTINGS_URL}/listings/{lid}")
                if resp.status_code != 200:
                    continue
                row = resp.json()
                lat, lng = row.get("lat"), row.get("lng")
                if lat is None or lng is None:
                    continue
                out[lid] = {"id": lid, "lat": float(lat), "lng": float(lng)}
            except Exception:
                continue
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "provider": ROUTING_PROVIDER,
        "mapbox_configured": bool(MAPBOX_ACCESS_TOKEN),
        "google_configured": bool(GOOGLE_MAPS_API_KEY),
        "max_stops": TOUR_MAX_STOPS,
    }


@app.post("/route/optimize")
async def route_optimize(req: OptimizeReq) -> dict[str, Any]:
    if len(req.listing_ids) > TOUR_MAX_STOPS:
        raise HTTPException(
            400,
            f"máximo {TOUR_MAX_STOPS} propiedades por recorrido",
        )
    coords = await _fetch_listing_coords(req.listing_ids)
    missing = [lid for lid in req.listing_ids if lid not in coords]
    if not coords:
        raise HTTPException(404, "ningún listing con coordenadas")

    stops = [coords[lid] for lid in req.listing_ids if lid in coords]
    ordered, legs, total_m = nearest_neighbor_order(
        req.origin.lat,
        req.origin.lng,
        stops,
        return_to_origin=req.return_to_origin,
    )
    total_drive_s = sum(int(leg.get("duration_s") or 0) for leg in legs)
    dwell_s = req.dwell_minutes * 60 * len(ordered)
    buffer_s = TOUR_TRAVEL_BUFFER_MINUTES * 60 * max(0, len(ordered) - 1)

    return {
        "provider": ROUTING_PROVIDER if ROUTING_PROVIDER != "haversine" else "haversine",
        "origin": req.origin.model_dump(),
        "ordered_listing_ids": ordered,
        "missing_listing_ids": missing,
        "legs": legs,
        "total_distance_m": round(total_m, 1),
        "total_drive_s": total_drive_s,
        "total_dwell_s": dwell_s,
        "total_buffer_s": buffer_s,
        "estimated_tour_s": total_drive_s + dwell_s + buffer_s,
        "dwell_minutes_per_stop": req.dwell_minutes,
    }


@app.post("/route/directions")
async def route_directions(req: DirectionsReq) -> dict[str, Any]:
    """Returns route geometry. Phase 6 prep: haversine legs only (no polyline yet)."""
    optimized = await route_optimize(
        OptimizeReq(
            origin=req.origin,
            listing_ids=req.listing_ids,
            return_to_origin=req.return_to_origin,
        )
    )
    if req.ordered_listing_ids:
        optimized["ordered_listing_ids"] = req.ordered_listing_ids
    optimized["polyline"] = None
    optimized["note"] = (
        "Polylines arrive in P6.2 when MAPBOX_ACCESS_TOKEN or "
        "GOOGLE_MAPS_API_KEY is configured."
    )
    return optimized
