"""
Inmuebles24 market adapter via Apify (requires APIFY_API_TOKEN).

Default actor returns naventcdn photo URLs:
  azzouzana/inmuebles24-scraper-pro-by-search-url

Legacy (listing-page thumbnails only — not recommended):
  juandiaz.mx/inmuebles24-scraper

Alternate with rich picture objects:
  ecomscrape/inmuebles24-property-listings-scraper

Set APIFY_INMUEBLES24_ACTOR in .env to switch.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from ..apify_client import fetch_apify_portal, proxy_configuration
from ..market_geo import expand_colonia_queries, expand_market_queries
from ..contact_extract import extract_contacts_from_raw
from ..media_extract import extract_listing_media
from ..portal_sanitize import posting_id_from_url, sanitize_portal_listing
from .inmuebles24_urls import inmuebles24_search_url

APIFY_ACTOR = os.getenv(
    "APIFY_INMUEBLES24_ACTOR",
    "azzouzana/inmuebles24-scraper-pro-by-search-url",
)
ActorProfile = Literal["search_url", "ecomscrape", "juandiaz"]
_POSTING_ID_RE = re.compile(r"(\d{6,})\.html")
_INMUEBLES24_ORIGIN = "https://www.inmuebles24.com"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _queries_path() -> Path:
    return Path(os.getenv("MARKET_QUERIES_PATH", "data/market_queries.json"))


def _colonias_queries_path() -> Path:
    return Path(os.getenv("MARKET_COLONIAS_QUERIES_PATH", "data/market_queries_colonias.json"))


class Inmuebles24Error(RuntimeError):
    pass


def actor_profile(actor: str | None = None) -> ActorProfile:
    """Infer Apify input/output shape from the configured actor slug."""
    slug = (actor or APIFY_ACTOR).lower()
    if "ecomscrape" in slug:
        return "ecomscrape"
    if any(token in slug for token in ("azzouzana", "search-url", "scraper-pro")):
        return "search_url"
    return "juandiaz"


def _absolute_inmuebles24_url(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if cleaned.startswith("http"):
        return cleaned.split("?")[0]
    if cleaned.startswith("/"):
        return f"{_INMUEBLES24_ORIGIN}{cleaned.split('?')[0]}"
    return cleaned


def _flatten_apify_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map actor-specific payloads onto keys normalize_inmuebles24_row expects."""
    out = dict(raw)

    source_url = _absolute_inmuebles24_url(
        str(raw.get("url") or raw.get("link") or raw.get("detailUrl") or "")
    )
    if source_url:
        out["url"] = source_url

    posting_id = str(
        raw.get("postingId")
        or raw.get("posting_id")
        or posting_id_from_url(source_url)
        or ""
    ).strip()
    if posting_id:
        out["postingId"] = posting_id

    if isinstance(raw.get("house_image"), str):
        out.setdefault("thumbnail", raw["house_image"])
    if isinstance(raw.get("images"), list):
        out.setdefault("media", raw["images"])

    price = raw.get("price_amount") or raw.get("price_mxn")
    if price is not None:
        out.setdefault("price", price)
        out.setdefault("currency", raw.get("price_currency") or "MXN")

    desc = raw.get("descriptionNormalized") or raw.get("description")
    if isinstance(desc, str) and desc.strip():
        out.setdefault("description", desc)

    prop_type = raw.get("realEstateType_name") or raw.get("property_type")
    if prop_type:
        out.setdefault("property_type", prop_type)

    lat = raw.get("postingLocation_postingGeolocation_geolocation_latitude")
    lng = raw.get("postingLocation_postingGeolocation_geolocation_longitude")
    if lat is not None:
        out.setdefault("lat", lat)
    if lng is not None:
        out.setdefault("lng", lng)

    nbhd = raw.get("postingLocation_location_name")
    city = raw.get("postingLocation_location_parent_name")
    if isinstance(nbhd, str) and nbhd.strip():
        out.setdefault("neighborhood", nbhd.strip())
    if isinstance(city, str) and city.strip():
        out.setdefault("city", city.strip())

    publisher = raw.get("publisher_name")
    if isinstance(publisher, str) and publisher.strip():
        out.setdefault("publisher_name", publisher.strip())

    visible = raw.get("visible_pictures")
    if isinstance(visible, dict) and isinstance(visible.get("pictures"), list):
        out.setdefault("pictures", visible["pictures"])

    loc = raw.get("posting_location")
    if isinstance(loc, dict):
        loc_name = loc.get("location") if isinstance(loc.get("location"), dict) else {}
        if isinstance(loc_name, dict):
            name = str(loc_name.get("name") or "")
            if name:
                out.setdefault("location", name)

    return out


def build_apify_run_input(
    query: dict[str, Any],
    *,
    profile: ActorProfile,
    max_items: int,
    proxy: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build actor-specific Apify POST body from a market query row."""
    if profile == "juandiaz":
        run_input: dict[str, Any] = {
            "city": query["city"],
            "propertyType": query.get("propertyType", "departamentos"),
            "transactionType": query.get("transactionType", "venta"),
            "maxItems": max_items,
        }
        if proxy:
            run_input["proxyConfiguration"] = proxy
        return run_input

    search_url = inmuebles24_search_url(query)

    if profile == "search_url":
        run_input = {"startUrl": search_url, "maxItems": max_items}
        if proxy:
            run_input["proxyConfiguration"] = proxy
        return run_input

    # ecomscrape
    run_input = {
        "urls": [search_url],
        "ignore_url_failures": True,
        "max_items_per_url": max_items,
        "max_retries_per_url": 2,
    }
    if proxy:
        run_input["proxy"] = proxy
    return run_input


def load_market_queries() -> list[dict[str, Any]]:
    path = _queries_path()
    if not path.is_file():
        raw: list[dict[str, Any]] = [
            {
                "city": "Ciudad de México",
                "propertyType": "departamentos",
                "transactionType": "venta",
                "state": "CDMX",
            },
        ]
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raw = []
    expanded = expand_market_queries(raw)
    colonias_path = _colonias_queries_path()
    if colonias_path.is_file():
        colonias_raw = json.loads(colonias_path.read_text(encoding="utf-8"))
        if isinstance(colonias_raw, list):
            expanded.extend(expand_colonia_queries(colonias_raw))
    return expanded


def normalize_inmuebles24_row(
    raw: dict[str, Any],
    *,
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map Apify row → catalog shape; geography/titles finalized in sanitize."""
    raw = _flatten_apify_row(raw)
    source_url = str(raw.get("url") or raw.get("link") or raw.get("detailUrl") or "").strip()
    posting_id = posting_id_from_url(source_url) or str(
        raw.get("id") or raw.get("postingId") or raw.get("posting_id") or ""
    ).strip()
    if not posting_id:
        return None

    loc = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    location_text = (
        str(raw.get("location") or "")
        if isinstance(raw.get("location"), str)
        else str(loc.get("name") or "")
    )

    price = raw.get("price_mxn") or raw.get("price") or raw.get("amount") or 0
    currency = str(raw.get("currency") or "MXN").upper()
    if currency == "USD" and price:
        price = int(float(price) * 18)
    price_mxn = int(float(price or 0))

    media = extract_listing_media(raw)

    publisher, agent_name = extract_contacts_from_raw(raw)

    row: dict[str, Any] = {
        "id": f"I24-{posting_id}",
        "title": str(raw.get("title") or raw.get("headline") or ""),
        "address": str(raw.get("address") or raw.get("full_address") or ""),
        "state": str(raw.get("state") or ""),
        "city": str(raw.get("city") or loc.get("city") or loc.get("municipality") or ""),
        "neighborhood": str(raw.get("neighborhood") or loc.get("neighborhood") or ""),
        "location": location_text,
        "lat": raw.get("lat") or raw.get("latitude") or loc.get("latitude"),
        "lng": raw.get("lng") or raw.get("longitude") or loc.get("longitude"),
        "price_mxn": price_mxn,
        "beds": int(raw.get("bedrooms") or raw.get("beds") or 0),
        "baths": float(raw.get("bathrooms") or raw.get("baths") or 0),
        "m2": float(raw.get("m2") or raw.get("area_m2") or raw.get("squareMeters") or 0),
        "type": str(raw.get("property_type") or raw.get("propertyType") or ""),
        "status": "available",
        "features": [],
        "description": str(raw.get("description") or "")[:2000],
        "source": "inmuebles24",
        "source_url": source_url,
        "publisher_name": publisher,
        "agent_name": agent_name,
        "media": media,
    }
    if media:
        row["thumbnail"] = media[0]

    return sanitize_portal_listing(row, query=query)


def _proxy_configuration() -> dict[str, Any] | None:
    return proxy_configuration()


async def fetch_inmuebles24_market(
    *,
    queries: list[dict[str, Any]] | None = None,
    max_items: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    queries = queries or load_market_queries()
    profile = actor_profile(APIFY_ACTOR)

    def _build_input(query: dict[str, Any], limit: int, proxy: dict[str, Any] | None) -> dict[str, Any]:
        return build_apify_run_input(
            query,
            profile=profile,
            max_items=limit,
            proxy=proxy,
        )

    def _normalize(raw: dict[str, Any], query: dict[str, Any] | None) -> dict[str, Any] | None:
        return normalize_inmuebles24_row(raw, query=query)

    listings, meta = await fetch_apify_portal(
        portal="inmuebles24",
        actor_slug=APIFY_ACTOR,
        queries=queries,
        build_run_input=_build_input,
        normalize_row=_normalize,
        flatten_row=_flatten_apify_row,
        search_url_for_query=(
            lambda q: inmuebles24_search_url(q) if profile != "juandiaz" else ""
        ),
        max_items=max_items,
        log_prefix=f"Inmuebles24 ({profile})",
        # Location/city actors and single-startUrl store actors stay 1:1 for now.
        batch_size=1,
    )
    meta["actor_profile"] = profile
    return listings, meta


def inmuebles24_enabled() -> bool:
    flag = os.getenv("INGEST_INMUEBLES24", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    portals = os.getenv("INGEST_PORTALS", "inmuebles24,vivanuncios").strip().lower()
    return "inmuebles24" in {part.strip() for part in portals.split(",") if part.strip()}
