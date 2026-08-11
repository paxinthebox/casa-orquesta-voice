"""
Mercado Libre Inmuebles adapter via Apify (requires APIFY_API_TOKEN).

Legacy actor (paid rental):
  ecomscrape/mercadolibre-product-search-scraper

Live custom actor (recommended):
  YOUR_USERNAME/mercadolibre-inmuebles-mx-scraper — set INGEST_MERCADOLIBRE_LIVE=1

Pass inmuebles.mercadolibre.com.mx list URLs; residential MX proxy required.
Enable with INGEST_PORTALS=...,mercadolibre
"""
from __future__ import annotations

import os
import re
from typing import Any

from ..apify_client import env_int, fetch_apify_portal, portal_batch_size
from ..media_extract import extract_listing_media
from ..portal_sanitize import sanitize_portal_listing
from .inmuebles24 import load_market_queries
from .mercadolibre_urls import mercadolibre_search_url

APIFY_LEGACY_ACTOR = "ecomscrape/mercadolibre-product-search-scraper"


def _live_actor_slug() -> str:
    return os.getenv("APIFY_MERCADOLIBRE_ACTOR", "").strip()


def _legacy_actor_slug() -> str:
    return os.getenv("APIFY_MERCADOLIBRE_LEGACY_ACTOR", APIFY_LEGACY_ACTOR).strip() or APIFY_LEGACY_ACTOR
_MLM_ID_RE = re.compile(r"MLM-?(\d{6,})", re.I)
_ML_HOST_RE = re.compile(r"https?://[^/]*mercadolibre\.com\.mx", re.I)


def mercadolibre_live_enabled() -> bool:
    flag = os.getenv("INGEST_MERCADOLIBRE_LIVE", "0").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def _active_actor_slug() -> str:
    if mercadolibre_live_enabled():
        live = _live_actor_slug()
        if live:
            return live
    return _legacy_actor_slug()


def mercadolibre_item_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = _MLM_ID_RE.search(url.strip())
    return match.group(1) if match else ""


def mercadolibre_item_id(raw: dict[str, Any]) -> str:
    for key in ("item_id", "itemId", "listing_id", "listingId", "id"):
        value = str(raw.get(key) or "").strip()
        if not value:
            continue
        match = _MLM_ID_RE.search(value)
        if match:
            return match.group(1)
        if value.isdigit() and len(value) >= 6:
            return value
    for key in ("url", "permalink", "link", "source_url"):
        found = mercadolibre_item_id_from_url(str(raw.get(key) or ""))
        if found:
            return found
    return ""


def _absolute_mercadolibre_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("http") and "mercadolibre" in cleaned.lower():
        return cleaned.split("#")[0].split("?")[0]
    return cleaned


def _component_value(components: list[Any], component_type: str) -> Any:
    for block in components:
        if not isinstance(block, dict) or block.get("type") != component_type:
            continue
        return block.get(component_type)
    return None


def _title_from_components(components: list[Any]) -> str:
    title_block = _component_value(components, "title")
    if isinstance(title_block, dict):
        return str(title_block.get("text") or "").strip()
    return ""


def _price_from_components(components: list[Any]) -> tuple[int, str]:
    price_block = _component_value(components, "price")
    if not isinstance(price_block, dict):
        return 0, "MXN"
    current = price_block.get("current_price")
    if isinstance(current, dict) and current.get("value") is not None:
        currency = str(current.get("currency") or "MXN").upper()
        return int(float(current["value"])), currency
    return 0, "MXN"


def _location_from_components(components: list[Any]) -> str:
    location_block = _component_value(components, "location")
    if isinstance(location_block, dict):
        return str(
            location_block.get("text")
            or location_block.get("name")
            or ""
        ).strip()
    attributes = _component_value(components, "attributes")
    if isinstance(attributes, list):
        for attr in attributes:
            if not isinstance(attr, dict):
                continue
            label = _fold(str(attr.get("text") or attr.get("name") or ""))
            if "ubic" in label or "location" in label:
                return str(attr.get("value") or attr.get("text") or "").strip()
    return ""


def _fold(text: str) -> str:
    return text.lower()


def _attrs_from_components(components: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    attributes = _component_value(components, "attributes")
    if not isinstance(attributes, list):
        return out
    for attr in attributes:
        if not isinstance(attr, dict):
            continue
        text = _fold(str(attr.get("text") or attr.get("name") or ""))
        value = attr.get("value") or attr.get("text")
        if "rec" in text and "mar" in text:
            out["bedrooms"] = value
        elif "bañ" in text or "bano" in text:
            out["bathrooms"] = value
        elif "m²" in text or "m2" in text or "metro" in text:
            out["m2"] = value
    return out


def _permalink_from_raw(raw: dict[str, Any], item_id: str) -> str:
    for key in ("permalink", "url", "link", "source_url", "product_url"):
        candidate = _absolute_mercadolibre_url(str(raw.get(key) or ""))
        if candidate and _ML_HOST_RE.search(candidate) and "click" not in candidate:
            return candidate
    if item_id:
        return f"https://inmuebles.mercadolibre.com.mx/MLM-{item_id}"
    return ""


def _flatten_mercadolibre_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map Apify actor payload → flat keys for normalize_mercadolibre_row."""
    if isinstance(raw.get("entity"), dict):
        entity = raw["entity"]
        flat = dict(raw)
        flat.update(entity)
        raw = flat

    components = raw.get("components") if isinstance(raw.get("components"), list) else []
    item_id = mercadolibre_item_id(raw)
    title = str(raw.get("title") or _title_from_components(components) or "").strip()
    price, currency = _price_from_components(components)
    if not price:
        price = int(float(raw.get("price") or raw.get("price_mxn") or 0))
        currency = str(raw.get("currency") or raw.get("currency_id") or "MXN").upper()

    location_text = str(
        raw.get("location")
        or raw.get("address")
        or _location_from_components(components)
        or ""
    ).strip()
    attrs = _attrs_from_components(components)
    source_url = _permalink_from_raw(raw, item_id)

    flat: dict[str, Any] = {
        "item_id": item_id,
        "url": source_url,
        "title": title,
        "description": str(raw.get("description") or ""),
        "price": price,
        "currency": currency,
        "location": location_text,
        "bedrooms": raw.get("bedrooms") or attrs.get("bedrooms"),
        "bathrooms": raw.get("bathrooms") or attrs.get("bathrooms"),
        "m2": raw.get("m2") or raw.get("area_m2") or attrs.get("m2"),
        "property_type": str(raw.get("property_type") or raw.get("type") or ""),
        "pictures": raw.get("pictures") or raw.get("images") or [],
    }
    if raw.get("thumbnail"):
        flat["thumbnail"] = raw["thumbnail"]
    elif raw.get("picture"):
        flat["thumbnail"] = raw["picture"]
    return flat


def _flatten_live_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize output from mercadolibre-inmuebles-mx-scraper."""
    url = _absolute_mercadolibre_url(
        str(raw.get("url") or raw.get("permalink") or raw.get("link") or "")
    )
    item_id = mercadolibre_item_id(raw)
    title = str(raw.get("title") or raw.get("name") or "").strip()
    location_text = str(raw.get("location") or raw.get("address") or "").strip()
    listing_mode = str(raw.get("listing_mode") or "").strip().lower()
    if listing_mode not in {"rent", "sale"}:
        listing_mode = "rent" if "/renta/" in url.lower() else "sale"

    return {
        "item_id": item_id,
        "url": url,
        "title": title,
        "description": str(raw.get("description") or ""),
        "location": location_text,
        "neighborhood": str(raw.get("neighborhood") or raw.get("colonia") or "").strip(),
        "city": str(raw.get("city") or raw.get("municipality") or "").strip(),
        "state": str(raw.get("state") or "").strip(),
        "price": raw.get("price") or raw.get("price_mxn") or 0,
        "currency": str(raw.get("currency") or raw.get("currency_id") or "MXN"),
        "bedrooms": raw.get("bedrooms") or raw.get("beds"),
        "bathrooms": raw.get("bathrooms") or raw.get("baths"),
        "m2": raw.get("area_m2") or raw.get("m2"),
        "property_type": str(raw.get("property_type") or raw.get("propertyType") or ""),
        "listing_mode": listing_mode,
        "lat": raw.get("lat") or raw.get("latitude"),
        "lng": raw.get("lng") or raw.get("longitude"),
        "thumbnail": raw.get("thumbnail") or raw.get("picture"),
        "live_scrape": True,
        "query_url": str(raw.get("query_url") or raw.get("search_url") or "").strip() or None,
    }


def _flatten_row(raw: dict[str, Any]) -> dict[str, Any]:
    if mercadolibre_live_enabled():
        return _flatten_live_row(raw)
    return _flatten_mercadolibre_row(raw)


def build_legacy_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    search_url = mercadolibre_search_url(query)
    run_input: dict[str, Any] = {
        "urls": [search_url],
        "ignore_url_failures": True,
        "max_items_per_url": max_items,
        "max_retries_per_url": env_int("APIFY_MERCADOLIBRE_MAX_RETRIES", 2),
    }
    if proxy:
        run_input["proxy"] = {
            **proxy,
            "apifyProxyCountry": "MX",
        }
    return run_input


def build_live_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    start_url = mercadolibre_search_url(query)
    max_pages = env_int("APIFY_MERCADOLIBRE_MAX_PAGES", 10)
    run_input: dict[str, Any] = {
        "startUrl": start_url,
        "maxItems": max_items,
        "maxPages": max_pages,
    }
    if proxy:
        run_input["proxyConfiguration"] = {
            **proxy,
            "apifyProxyCountry": "MX",
        }
    return run_input


def build_apify_run_input(
    query: dict[str, Any],
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    if mercadolibre_live_enabled():
        return build_live_apify_run_input(query, max_items, proxy)
    return build_legacy_apify_run_input(query, max_items, proxy)


def normalize_mercadolibre_row(
    raw: dict[str, Any],
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    flat = _flatten_row(raw)
    item_id = str(flat.get("item_id") or "").strip()
    if not item_id:
        return None

    source_url = str(flat.get("url") or "").strip()
    price = flat.get("price_mxn") or flat.get("price") or 0
    currency = str(flat.get("currency") or "MXN").upper()
    if currency == "USD" and price:
        price = int(float(price) * 18)
    price_mxn = int(float(price or 0))

    media = extract_listing_media(flat)
    location_text = str(flat.get("location") or "").strip()

    city = ""
    neighborhood = ""
    if query:
        neighborhood = str(query.get("colonia") or query.get("neighborhood") or "").strip()
        city = str(query.get("city") or query.get("municipality") or "").strip()

    if flat.get("live_scrape"):
        neighborhood = neighborhood or str(flat.get("neighborhood") or "").strip()
        city = city or str(flat.get("city") or "").strip()
        query_state = str((query or {}).get("state") or "").strip()
        state = str(flat.get("state") or query_state).strip()
    else:
        state = str((query or {}).get("state") or "")

    txn = str((query or {}).get("transactionType") or "").strip().lower()
    listing_mode = str(flat.get("listing_mode") or "").strip().lower()
    if listing_mode not in {"rent", "sale"}:
        listing_mode = "rent" if txn in {"renta", "rent", "rental", "alquiler"} else "sale"

    row: dict[str, Any] = {
        "id": f"ML-{item_id}",
        "title": str(flat.get("title") or ""),
        "address": location_text,
        "state": state,
        "city": city,
        "neighborhood": neighborhood,
        "location": location_text or neighborhood,
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
        "source": "mercadolibre",
        "source_url": source_url,
        "media": media,
    }
    if media:
        row["thumbnail"] = media[0]

    return sanitize_portal_listing(row, query=query)


async def fetch_mercadolibre_market(
    *,
    queries: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if mercadolibre_live_enabled() and not _live_actor_slug():
        return [], {
            "source": "mercadolibre",
            "count": 0,
            "queries": [],
            "error": "INGEST_MERCADOLIBRE_LIVE=1 but APIFY_MERCADOLIBRE_ACTOR is not set",
            "reason": "INGEST_MERCADOLIBRE_LIVE=1 but APIFY_MERCADOLIBRE_ACTOR is not set",
        }
    queries = queries or load_market_queries()
    ml_timeout = env_int("APIFY_MERCADOLIBRE_TIMEOUT_SEC", 360)
    batch = portal_batch_size("mercadolibre", default=15)
    return await fetch_apify_portal(
        portal="mercadolibre",
        actor_slug=_active_actor_slug(),
        queries=queries,
        build_run_input=build_apify_run_input,
        normalize_row=normalize_mercadolibre_row,
        flatten_row=_flatten_row,
        search_url_for_query=mercadolibre_search_url,
        max_items=max_items,
        timeout_sec=ml_timeout,
        log_prefix="MercadoLibre",
        batch_size=batch,
    )


def mercadolibre_enabled() -> bool:
    flag = os.getenv("INGEST_MERCADOLIBRE", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    portals = os.getenv("INGEST_PORTALS", "inmuebles24,vivanuncios").strip().lower()
    return "mercadolibre" in {part.strip() for part in portals.split(",") if part.strip()}
