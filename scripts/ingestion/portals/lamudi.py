"""
Lamudi MX — inventory export (default) or live list-page scrape (opt-in).

Default: thescrapelab/the-ultimate-property-scraper inventory export filtered by
areaQuery + title/geo validation.

Live (``INGEST_LAMUDI_LIVE=1``): Apify actor at ``APIFY_LAMUDI_ACTOR`` receives
``startUrl`` from ``lamudi_search_url()`` (see ``lamudi_urls.py``). Requires a
custom MX residential actor — no public Apify store actor yet.

Enable portal with INGEST_PORTALS=...,lamudi
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from ..apify_client import fetch_apify_portal, env_int, portal_batch_size
from ..portal_sanitize import sanitize_portal_listing
from .inmuebles24 import load_market_queries
from .lamudi_geo import enrich_lamudi_location, parse_lamudi_title_location
from .lamudi_queries import (
    fix_mojibake,
    lamudi_area_query,
    lamudi_market,
    lamudi_property_type_query,
    lamudi_search_label,
    lamudi_title_matches_query,
)
from .lamudi_urls import lamudi_search_url

APIFY_INVENTORY_ACTOR = os.getenv(
    "APIFY_LAMUDI_INVENTORY_ACTOR",
    "thescrapelab/the-ultimate-property-scraper",
)
APIFY_LIVE_ACTOR = os.getenv("APIFY_LAMUDI_ACTOR", "").strip()
_LAMUDI_ORIGIN = "https://www.lamudi.com.mx"
_LISTING_ID_RE = re.compile(r"/detalle/([^/?#]+)")


def lamudi_listing_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = _LISTING_ID_RE.search(url.strip())
    return match.group(1) if match else ""


def lamudi_live_enabled() -> bool:
    flag = os.getenv("INGEST_LAMUDI_LIVE", "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _active_actor_slug() -> str:
    if lamudi_live_enabled():
        return APIFY_LIVE_ACTOR
    return APIFY_INVENTORY_ACTOR


def _search_hint(query: dict[str, Any]) -> str:
    if lamudi_live_enabled():
        return lamudi_search_url(query)
    return lamudi_search_label(query)


def _safe_lamudi_id(listing_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]", "-", listing_id.strip())
    if len(cleaned) <= 48:
        return cleaned
    digest = hashlib.sha1(cleaned.encode()).hexdigest()[:12]
    return f"{cleaned[:32]}-{digest}"


def _normalize_state_label(raw: str) -> str:
    folded = raw.strip().lower()
    if folded in {"cdmx", "ciudad de mexico", "ciudad de méxico", "distrito federal"}:
        return "CDMX"
    if folded == "morelos":
        return "Morelos"
    return raw.strip()


def _listing_mode_from_raw(raw: dict[str, Any], url: str) -> str:
    txn = str(
        raw.get("listing_mode")
        or raw.get("transactionType")
        or raw.get("transaction_type")
        or raw.get("market")
        or ""
    ).strip().lower()
    if txn in {"rent", "renta", "to-rent", "rental", "alquiler", "for-rent"}:
        return "rent"
    if txn in {"sale", "venta", "for-sale"}:
        return "sale"
    if "/for-rent/" in url or "/en-renta/" in url:
        return "rent"
    return "sale"


def _property_type_label(raw: dict[str, Any]) -> str:
    prop_type = str(
        raw.get("type")
        or raw.get("propertyType")
        or raw.get("property_type")
        or ""
    ).strip().lower()
    type_map = {
        "house": "casa",
        "casa": "casa",
        "casas": "casa",
        "apartment": "departamento",
        "departamento": "departamento",
        "departamentos": "departamento",
        "land": "terreno",
        "terreno": "terreno",
        "office": "oficina",
    }
    return type_map.get(prop_type, prop_type or "inmueble")


def _flatten_inventory_row(raw: dict[str, Any]) -> dict[str, Any]:
    listing_id = str(raw.get("listing_id") or "").strip()
    url = str(raw.get("url") or "").strip()
    if not listing_id:
        listing_id = lamudi_listing_id_from_url(url)

    title = fix_mojibake(str(raw.get("title") or ""))
    description = fix_mojibake(str(raw.get("description") or ""))
    nbhd, city, state = parse_lamudi_title_location(title)
    if not nbhd and description:
        nbhd, city, state = parse_lamudi_title_location(description[:280])

    listing_mode = _listing_mode_from_raw(raw, url)

    return {
        "listing_id": listing_id,
        "url": url,
        "title": title,
        "description": description,
        "neighborhood": nbhd or str(raw.get("suburb") or raw.get("location") or ""),
        "city": city or str(raw.get("city") or ""),
        "state": state or _normalize_state_label(str(raw.get("province_name") or "")),
        "price": raw.get("price") or 0,
        "currency": str(raw.get("currency_code") or "MXN"),
        "beds": raw.get("bedrooms"),
        "baths": raw.get("bathrooms"),
        "m2": raw.get("floor_size_m2") or raw.get("erf_size_m2"),
        "type": _property_type_label(raw),
        "listing_mode": listing_mode,
        "agent_name": str(raw.get("agent") or "").strip(),
        "publisher_name": str(raw.get("agent") or "").strip(),
        "last_seen_at": raw.get("last_seen_at"),
        "live_scrape": False,
    }


def _flatten_live_row(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize output from a Lamudi MX live list/detail Apify actor.

    Expected fields (snake_case or camelCase): url, title, price, bedrooms,
    bathrooms, area_m2 / floor_size_m2, propertyType, city, neighborhood,
    state, description, lat, lng.
    """
    url = str(
        raw.get("url")
        or raw.get("detailUrl")
        or raw.get("detail_url")
        or raw.get("link")
        or ""
    ).strip()
    listing_id = str(
        raw.get("listingId")
        or raw.get("listing_id")
        or raw.get("id")
        or ""
    ).strip()
    if not listing_id:
        listing_id = lamudi_listing_id_from_url(url)

    title = fix_mojibake(str(raw.get("title") or raw.get("name") or ""))
    description = fix_mojibake(str(raw.get("description") or ""))

    neighborhood = str(
        raw.get("neighborhood")
        or raw.get("colonia")
        or raw.get("suburb")
        or raw.get("location")
        or ""
    ).strip()
    city = str(raw.get("city") or raw.get("municipality") or "").strip()
    state = _normalize_state_label(
        str(raw.get("state") or raw.get("province") or raw.get("region") or "")
    )

    if not neighborhood and title:
        nbhd, parsed_city, parsed_state = parse_lamudi_title_location(title)
        neighborhood = neighborhood or nbhd
        city = city or parsed_city
        state = state or parsed_state

    return {
        "listing_id": listing_id,
        "url": url,
        "title": title,
        "description": description,
        "neighborhood": neighborhood,
        "city": city,
        "state": state,
        "price": raw.get("price") or raw.get("price_mxn") or 0,
        "currency": str(raw.get("currency") or raw.get("currency_code") or "MXN"),
        "beds": raw.get("bedrooms") or raw.get("beds"),
        "baths": raw.get("bathrooms") or raw.get("baths"),
        "m2": raw.get("area_m2") or raw.get("floor_size_m2") or raw.get("m2"),
        "type": _property_type_label(raw),
        "listing_mode": _listing_mode_from_raw(raw, url),
        "agent_name": str(raw.get("agent_name") or raw.get("agent") or "").strip(),
        "publisher_name": str(
            raw.get("publisher_name") or raw.get("publisher") or raw.get("agent") or ""
        ).strip(),
        "last_seen_at": None,
        "live_scrape": True,
        "lat": raw.get("lat") or raw.get("latitude"),
        "lng": raw.get("lng") or raw.get("longitude"),
        "query_url": str(raw.get("query_url") or raw.get("search_url") or "").strip() or None,
    }


def _live_geo_trustworthy(flat: dict[str, Any], query: dict[str, Any]) -> bool:
    """Live scrape rows with portal geo can skip fuzzy inventory title matching."""
    if not flat.get("live_scrape"):
        return False
    expected_state = str(query.get("state") or "").strip()
    state = str(flat.get("state") or "").strip()
    city = str(flat.get("city") or "").strip()
    if not city and not state:
        return False
    if expected_state and state and state != expected_state:
        return False
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    if colonia:
        nbhd = str(flat.get("neighborhood") or "").strip()
        if not nbhd:
            return False
    return True


def build_inventory_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    run_input: dict[str, Any] = {
        "countries": ["MX"],
        "websites": ["lamudimxproperty"],
        "markets": [lamudi_market(query)],
        "areaQuery": lamudi_area_query(query),
        "propertyTypeQuery": lamudi_property_type_query(query),
        "limit": max_items,
        "listingStatus": ["active"],
        "orderBy": "last_seen_desc",
    }
    _ = proxy
    return run_input


def build_live_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    max_pages = env_int("APIFY_LAMUDI_MAX_PAGES", 10)
    run_input: dict[str, Any] = {
        "startUrl": lamudi_search_url(query),
        "maxItems": max_items,
        "maxPages": max_pages,
    }
    if proxy:
        run_input["proxyConfiguration"] = proxy
    return run_input


def build_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    if lamudi_live_enabled():
        return build_live_apify_run_input(query, max_items, proxy)
    return build_inventory_apify_run_input(query, max_items, proxy)


def normalize_lamudi_row(
    raw: dict[str, Any],
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if lamudi_live_enabled():
        flat = enrich_lamudi_location(_flatten_live_row(raw))
    else:
        flat = enrich_lamudi_location(_flatten_inventory_row(raw))

    title = str(flat.get("title") or "")
    if query and not _live_geo_trustworthy(flat, query) and not lamudi_title_matches_query(
        title, query
    ):
        return None

    listing_id = str(flat.get("listing_id") or "").strip()
    source_url = str(flat.get("url") or "").strip()
    if not listing_id:
        listing_id = lamudi_listing_id_from_url(source_url)
    if not listing_id:
        return None

    price = int(float(flat.get("price") or 0))
    neighborhood = str(flat.get("neighborhood") or "").strip()
    city = str(flat.get("city") or "").strip()
    state = str(flat.get("state") or "").strip()

    row: dict[str, Any] = {
        "id": f"LAM-{_safe_lamudi_id(listing_id)}",
        "title": title[:200] if title else f"Propiedad {neighborhood}".strip(),
        "address": ", ".join(p for p in (neighborhood, city, state) if p),
        "state": state,
        "city": city,
        "neighborhood": neighborhood,
        "location": neighborhood,
        "price_mxn": price,
        "beds": int(flat.get("beds") or 0),
        "baths": float(flat.get("baths") or 0),
        "m2": float(flat.get("m2") or 0),
        "type": str(flat.get("type") or ""),
        "listing_mode": flat.get("listing_mode") or "sale",
        "status": "available",
        "features": [],
        "description": str(flat.get("description") or "")[:2000],
        "source": "lamudi",
        "source_url": source_url or f"{_LAMUDI_ORIGIN}/detalle/{listing_id}",
        "publisher_name": flat.get("publisher_name") or None,
        "agent_name": flat.get("agent_name") or None,
        "inventory_last_seen_at": flat.get("last_seen_at"),
    }
    lat = flat.get("lat")
    lng = flat.get("lng")
    if lat is not None and lng is not None:
        try:
            row["lat"] = float(lat)
            row["lng"] = float(lng)
        except (TypeError, ValueError):
            pass

    return sanitize_portal_listing(row, query=query)


async def fetch_lamudi_market(
    *,
    queries: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries = queries or load_market_queries()
    actor = _active_actor_slug()
    if lamudi_live_enabled() and not actor:
        return [], {
            "skipped": True,
            "reason": "INGEST_LAMUDI_LIVE=1 but APIFY_LAMUDI_ACTOR is not set",
            "mode": "live",
        }

    flatten = _flatten_live_row if lamudi_live_enabled() else _flatten_inventory_row
    mode_label = "live" if lamudi_live_enabled() else "inventory"
    # Inventory actor is areaQuery-based (no URL list) — keep one query per run.
    batch = (
        portal_batch_size("lamudi", default=15)
        if lamudi_live_enabled()
        else 1
    )

    listings, meta = await fetch_apify_portal(
        portal="lamudi",
        actor_slug=actor,
        queries=queries,
        build_run_input=build_apify_run_input,
        normalize_row=normalize_lamudi_row,
        flatten_row=flatten,
        search_url_for_query=_search_hint,
        max_items=max_items,
        log_prefix=f"Lamudi ({mode_label})",
        batch_size=batch,
    )
    meta["mode"] = mode_label
    return listings, meta


def lamudi_enabled() -> bool:
    flag = os.getenv("INGEST_LAMUDI", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    portals = os.getenv("INGEST_PORTALS", "inmuebles24,vivanuncios").strip().lower()
    return "lamudi" in {part.strip() for part in portals.split(",") if part.strip()}
