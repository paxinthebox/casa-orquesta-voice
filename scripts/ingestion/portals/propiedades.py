"""
Propiedades.com market adapter via Apify (requires APIFY_API_TOKEN).

Default actor: fatihtahta/propiedades-scraper

Runs the full CDMX + Morelos query grid (borough / city / colonia), up to
MARKET_MAX_ITEMS_PER_QUERY listings per Apify call.
Enable with INGEST_PORTALS=...,propiedades
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..apify_client import fetch_apify_portal
from ..media_extract import extract_listing_media
from ..portal_sanitize import posting_id_from_url, sanitize_portal_listing
from .inmuebles24 import load_market_queries
from .propiedades_queries import (
    propiedades_deal_type,
    propiedades_location,
    propiedades_market_queries,
    propiedades_property_type,
    propiedades_search_label,
)

APIFY_ACTOR = os.getenv("APIFY_PROPIEDADES_ACTOR", "fatihtahta/propiedades-scraper")
_PROP_ORIGIN = "https://propiedades.com"
_LISTING_ID_RE = re.compile(r"-(\d{6,})(?:\?|$|/)")


def propiedades_listing_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = _LISTING_ID_RE.search(url.strip())
    return match.group(1) if match else posting_id_from_url(url)


def _absolute_propiedades_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("http"):
        return cleaned.split("?")[0]
    if cleaned.startswith("/"):
        return f"{_PROP_ORIGIN}{cleaned.split('?')[0]}"
    return cleaned


def _flatten_propiedades_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map nested actor payload → flat keys for normalize_propiedades_row."""
    if not isinstance(raw.get("entity"), dict):
        return dict(raw)

    entity = raw["entity"]
    listing = raw.get("listing") if isinstance(raw.get("listing"), dict) else {}
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    prop = raw.get("property") if isinstance(raw.get("property"), dict) else {}
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}

    listing_id = str(
        listing.get("listing_id")
        or raw.get("record_id")
        or ""
    ).strip()
    source_url = _absolute_propiedades_url(str(entity.get("url") or ""))
    if not listing_id and source_url:
        listing_id = propiedades_listing_id_from_url(source_url)

    coords = loc.get("coordinates") if isinstance(loc.get("coordinates"), dict) else {}
    txn = str(listing.get("transaction_type") or "").strip().lower()
    price = pricing.get("sale_price") or pricing.get("price") or pricing.get("rental_price") or 0

    pictures: list[str] = []
    main_image = media.get("main_image_url") or (
        media.get("main_image", {}).get("url")
        if isinstance(media.get("main_image"), dict)
        else None
    )
    if isinstance(main_image, str) and main_image.startswith("http"):
        pictures.append(main_image)

    flat: dict[str, Any] = {
        "listing_id": listing_id,
        "url": source_url,
        "title": str(entity.get("title") or ""),
        "description": str(entity.get("description") or ""),
        "price": price,
        "currency": str(pricing.get("currency") or "MXN"),
        "neighborhood": str(loc.get("colony") or loc.get("colonia") or ""),
        "city": str(loc.get("municipality") or loc.get("city") or ""),
        "state": str(loc.get("state") or ""),
        "address": str(loc.get("display_address") or loc.get("short_address") or ""),
        "lat": coords.get("latitude"),
        "lng": coords.get("longitude"),
        "bedrooms": prop.get("bedrooms"),
        "baths": prop.get("bathrooms"),
        "m2": prop.get("floor_area_sqm"),
        "property_type": str(
            prop.get("property_subtype")
            or listing.get("listing_type")
            or prop.get("property_type")
            or ""
        ),
        "transaction_type": txn,
        "pictures": pictures,
    }
    if pictures:
        flat["thumbnail"] = pictures[0]
    return flat


def build_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    location = propiedades_location(query)
    run_input: dict[str, Any] = {
        "location": [location],
        "deal_type": propiedades_deal_type(query),
        "property_type": propiedades_property_type(query),
        "limit": max_items,
    }
    if proxy:
        run_input["proxyConfiguration"] = proxy
    return run_input


def normalize_propiedades_row(
    raw: dict[str, Any],
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    flat = _flatten_propiedades_row(raw)
    source_url = str(flat.get("url") or "").strip()
    listing_id = str(flat.get("listing_id") or propiedades_listing_id_from_url(source_url)).strip()
    if not listing_id:
        return None

    price = flat.get("price_mxn") or flat.get("price") or 0
    currency = str(flat.get("currency") or "MXN").upper()
    if currency == "USD" and price:
        price = int(float(price) * 18)
    price_mxn = int(float(price or 0))

    media = extract_listing_media(flat)
    neighborhood = str(flat.get("neighborhood") or "").strip()
    city = str(flat.get("city") or "").strip()

    txn = str(flat.get("transaction_type") or "").strip().lower()
    listing_mode = "rent" if txn in {"renta", "rent", "rental", "alquiler"} else "sale"

    row: dict[str, Any] = {
        "id": f"PROP-{listing_id}",
        "title": str(flat.get("title") or ""),
        "address": str(flat.get("address") or ""),
        "state": str(flat.get("state") or ""),
        "city": city,
        "neighborhood": neighborhood,
        "location": neighborhood,
        "lat": flat.get("lat") or flat.get("latitude"),
        "lng": flat.get("lng") or flat.get("longitude"),
        "price_mxn": price_mxn,
        "beds": int(flat.get("bedrooms") or flat.get("beds") or 0),
        "baths": float(flat.get("bathrooms") or flat.get("baths") or 0),
        "m2": float(flat.get("m2") or flat.get("area_m2") or 0),
        "type": str(flat.get("property_type") or flat.get("propertyType") or ""),
        "listing_mode": listing_mode,
        "status": "available",
        "features": [],
        "description": str(flat.get("description") or "")[:2000],
        "source": "propiedades",
        "source_url": source_url,
        "media": media,
    }
    if media:
        row["thumbnail"] = media[0]

    return sanitize_portal_listing(row, query=query)


async def fetch_propiedades_market(
    *,
    queries: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries = propiedades_market_queries(queries or load_market_queries())
    if not queries:
        return [], {
            "skipped": True,
            "reason": "no CDMX/Morelos queries",
            "listing_count": 0,
            "queries": [],
        }
    return await fetch_apify_portal(
        portal="propiedades",
        actor_slug=APIFY_ACTOR,
        queries=queries,
        build_run_input=build_apify_run_input,
        normalize_row=normalize_propiedades_row,
        flatten_row=_flatten_propiedades_row,
        search_url_for_query=propiedades_search_label,
        max_items=max_items,
        log_prefix="Propiedades",
        batch_size=1,
    )


def propiedades_enabled() -> bool:
    flag = os.getenv("INGEST_PROPIEDADES", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    portals = os.getenv("INGEST_PORTALS", "inmuebles24,vivanuncios").strip().lower()
    return "propiedades" in {part.strip() for part in portals.split(",") if part.strip()}
