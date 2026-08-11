"""
Vivanuncios market adapter via Apify (requires APIFY_API_TOKEN).

Default (store):
  stealth_mode/vivanuncios-property-search-scraper

Live custom (recommended for full-grid multi-URL):
  YOUR_USERNAME/vivanuncios-mx-scraper — set INGEST_VIVANUNCIOS_LIVE=1

Enable with INGEST_PORTALS=inmuebles24,vivanuncios (vivanuncios is on by default when token set).
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..apify_client import env_int, fetch_apify_portal, portal_batch_size
from ..contact_extract import extract_contacts_from_raw
from ..media_extract import extract_listing_media
from ..portal_sanitize import sanitize_portal_listing
from .inmuebles24 import load_market_queries
from .vivanuncios_urls import vivanuncios_search_url

APIFY_STORE_ACTOR = "stealth_mode/vivanuncios-property-search-scraper"
_VA_ORIGIN = "https://www.vivanuncios.com.mx"
_VA_POSTING_ID_RE = re.compile(r"/(\d{6,})(?:\.html)?(?:\?|$|/)")


def vivanuncios_live_enabled() -> bool:
    flag = os.getenv("INGEST_VIVANUNCIOS_LIVE", "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _active_actor_slug() -> str:
    if vivanuncios_live_enabled():
        return (
            os.getenv("APIFY_VIVANUNCIOS_LIVE_ACTOR", "").strip()
            or os.getenv("APIFY_VIVANUNCIOS_ACTOR", "").strip()
            or APIFY_STORE_ACTOR
        )
    return os.getenv("APIFY_VIVANUNCIOS_ACTOR", APIFY_STORE_ACTOR).strip() or APIFY_STORE_ACTOR


def vivanuncios_posting_id_from_url(url: str) -> str:
    if not url:
        return ""
    cleaned = url.strip()
    if cleaned.startswith("/"):
        cleaned = f"{_VA_ORIGIN}{cleaned}"
    match = _VA_POSTING_ID_RE.search(cleaned)
    return match.group(1) if match else ""


def _absolute_vivanuncios_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("http"):
        return cleaned.split("?")[0]
    if cleaned.startswith("/"):
        return f"{_VA_ORIGIN}{cleaned.split('?')[0]}"
    return cleaned


def _location_chain(loc: dict[str, Any] | None) -> tuple[str, str, str]:
    """Return neighborhood, city/municipality, state-ish label from posting_location."""
    if not isinstance(loc, dict):
        return "", "", ""
    inner = loc.get("location")
    if not isinstance(inner, dict):
        return "", "", ""
    neighborhood = str(inner.get("name") or "").strip()
    city = ""
    state = ""
    parent = inner.get("parent")
    if isinstance(parent, dict):
        city = str(parent.get("name") or "").strip()
        gparent = parent.get("parent")
        if isinstance(gparent, dict):
            state = str(gparent.get("name") or gparent.get("acronym") or "").strip()
    return neighborhood, city, state


def _flatten_apify_row(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    query_url = str(raw.get("query_url") or raw.get("search_url") or "").strip()
    if query_url:
        out["query_url"] = query_url

    source_url = _absolute_vivanuncios_url(
        str(raw.get("url") or raw.get("link") or raw.get("detailUrl") or "")
    )
    if source_url:
        out["url"] = source_url

    posting_id = str(
        raw.get("posting_id")
        or raw.get("postingId")
        or vivanuncios_posting_id_from_url(source_url)
        or ""
    ).strip()
    if posting_id:
        out["posting_id"] = posting_id

    loc = raw.get("posting_location")
    if isinstance(loc, dict):
        out.setdefault("posting_location", loc)
        nbhd, city, state = _location_chain(loc)
        if nbhd:
            out.setdefault("neighborhood", nbhd)
        if city:
            out.setdefault("city", city)
        if state:
            out.setdefault("state", state)
        geo = loc.get("posting_geolocation")
        if isinstance(geo, dict):
            geoloc = geo.get("geolocation")
            if isinstance(geoloc, dict):
                out.setdefault("lat", geoloc.get("latitude"))
                out.setdefault("lng", geoloc.get("longitude"))

    price_ops = raw.get("price_operation_types")
    if isinstance(price_ops, list):
        for block in price_ops:
            if not isinstance(block, dict):
                continue
            prices = block.get("prices")
            if not isinstance(prices, list) or not prices:
                continue
            first = prices[0]
            if isinstance(first, dict) and first.get("amount") is not None:
                out.setdefault("price", first.get("amount"))
                out.setdefault("currency", "MXN")
                break

    if isinstance(raw.get("description_normalized"), str):
        out.setdefault("description", raw["description_normalized"])

    prop = raw.get("real_estate_type")
    if isinstance(prop, dict) and prop.get("name"):
        out.setdefault("property_type", prop["name"])

    visible = raw.get("visible_pictures")
    if isinstance(visible, dict) and isinstance(visible.get("pictures"), list):
        out.setdefault("pictures", visible["pictures"])
    if isinstance(raw.get("images"), list):
        out.setdefault("media", raw["images"])

    publisher = raw.get("publisher_name") or raw.get("publisher")
    if isinstance(publisher, str) and publisher.strip():
        out.setdefault("publisher_name", publisher.strip())

    return out


def build_store_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    search_url = vivanuncios_search_url(query)
    run_input: dict[str, Any] = {
        "urls": [search_url],
        "ignore_url_failures": True,
        "max_items_per_url": max_items,
    }
    if proxy:
        run_input["proxyConfiguration"] = proxy
    return run_input


def build_live_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    search_url = vivanuncios_search_url(query)
    max_pages = env_int("APIFY_VIVANUNCIOS_MAX_PAGES", 10)
    run_input: dict[str, Any] = {
        "startUrl": search_url,
        "maxItems": max_items,
        "maxItemsPerUrl": max_items,
        "maxPages": max_pages,
        "ignore_url_failures": True,
    }
    if proxy:
        run_input["proxyConfiguration"] = proxy
    return run_input


def build_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    if vivanuncios_live_enabled():
        return build_live_apify_run_input(query, max_items, proxy)
    return build_store_apify_run_input(query, max_items, proxy)


def normalize_vivanuncios_row(
    raw: dict[str, Any],
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    raw = _flatten_apify_row(raw)
    source_url = str(raw.get("url") or "").strip()
    posting_id = str(raw.get("posting_id") or vivanuncios_posting_id_from_url(source_url)).strip()
    if not posting_id:
        return None

    nbhd, city, raw_state = _location_chain(
        raw.get("posting_location") if isinstance(raw.get("posting_location"), dict) else None
    )
    neighborhood = str(raw.get("neighborhood") or nbhd or "").strip()
    city = str(raw.get("city") or city or "").strip()

    price = raw.get("price_mxn") or raw.get("price") or raw.get("amount") or 0
    currency = str(raw.get("currency") or "MXN").upper()
    if currency == "USD" and price:
        price = int(float(price) * 18)
    price_mxn = int(float(price or 0))

    media = extract_listing_media(raw)
    publisher, agent_name = extract_contacts_from_raw(raw)

    row: dict[str, Any] = {
        "id": f"VA-{posting_id}",
        "title": str(raw.get("title") or raw.get("headline") or ""),
        "address": str(raw.get("address") or raw.get("full_address") or ""),
        "state": str(raw.get("state") or raw_state or ""),
        "city": city,
        "neighborhood": neighborhood,
        "location": neighborhood,
        "lat": raw.get("lat") or raw.get("latitude"),
        "lng": raw.get("lng") or raw.get("longitude"),
        "price_mxn": price_mxn,
        "beds": int(raw.get("bedrooms") or raw.get("beds") or 0),
        "baths": float(raw.get("bathrooms") or raw.get("baths") or 0),
        "m2": float(raw.get("m2") or raw.get("area_m2") or raw.get("squareMeters") or 0),
        "type": str(raw.get("property_type") or raw.get("propertyType") or ""),
        "status": "available",
        "features": [],
        "description": str(raw.get("description") or "")[:2000],
        "source": "vivanuncios",
        "source_url": source_url,
        "publisher_name": publisher,
        "agent_name": agent_name,
        "media": media,
    }
    if media:
        row["thumbnail"] = media[0]

    return sanitize_portal_listing(row, query=query)


async def fetch_vivanuncios_market(
    *,
    queries: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries = queries or load_market_queries()
    actor = _active_actor_slug()
    va_timeout = env_int("APIFY_VIVANUNCIOS_TIMEOUT_SEC", 480)
    # Store + live custom actors both accept multi-URL batches.
    batch = portal_batch_size("vivanuncios", default=15)
    mode = "live" if vivanuncios_live_enabled() else "store"
    listings, meta = await fetch_apify_portal(
        portal="vivanuncios",
        actor_slug=actor,
        queries=queries,
        build_run_input=build_apify_run_input,
        normalize_row=normalize_vivanuncios_row,
        flatten_row=_flatten_apify_row,
        search_url_for_query=vivanuncios_search_url,
        max_items=max_items,
        timeout_sec=va_timeout,
        log_prefix=f"Vivanuncios ({mode})",
        batch_size=batch,
    )
    meta["mode"] = mode
    return listings, meta


def vivanuncios_enabled() -> bool:
    flag = os.getenv("INGEST_VIVANUNCIOS", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    portals = os.getenv("INGEST_PORTALS", "inmuebles24,vivanuncios").strip().lower()
    return "vivanuncios" in {part.strip() for part in portals.split(",") if part.strip()}
