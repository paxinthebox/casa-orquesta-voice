"""Parse Lamudi.com.mx SERP pages (JSON-LD ItemList) for live ingest."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_LISTING_ID_RE = re.compile(r"/detalle/([^/?#]+)")
_RESIDENTIAL_TYPES = {
    "apartment",
    "house",
    "singlefamilyresidence",
    "condominium",
    "realestatelisting",
    "product",
    "landform",
    "accommodation",
}


def listing_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = _LISTING_ID_RE.search(url.strip())
    return match.group(1) if match else ""


def _normalize_state(raw: str) -> str:
    folded = raw.strip().lower()
    if folded in {"cdmx", "ciudad de mexico", "ciudad de méxico", "distrito federal"}:
        return "CDMX"
    if folded == "morelos":
        return "Morelos"
    return raw.strip()


def _property_type(schema_type: str, title: str) -> str:
    raw = (schema_type or "").lower()
    name = (title or "").lower()
    if raw == "apartment" or "departamento" in name or "depto" in name:
        return "departamento"
    if raw in {"house", "singlefamilyresidence"} or "casa" in name:
        return "casa"
    if "terreno" in name or "lote" in name or raw == "landform":
        return "terreno"
    if raw == "realestatelisting":
        if "departamento" in name:
            return "departamento"
        if "casa" in name:
            return "casa"
        if "terreno" in name or "lote" in name:
            return "terreno"
    return "inmueble"


def _neighborhood_from_address(address: dict[str, Any]) -> str:
    street = str(address.get("streetAddress") or "").strip()
    if street:
        parts = [p.strip() for p in street.split(",") if p.strip()]
        if len(parts) >= 2:
            return parts[1]
        if parts:
            return parts[0]
    return ""


def _listing_mode(start_url: str) -> str:
    lowered = start_url.lower()
    if "/for-rent/" in lowered or "en-renta" in lowered:
        return "rent"
    return "sale"


def _page_url(start_url: str, page: int) -> str:
    if page <= 1:
        return start_url.rstrip("/") + ("/" if not start_url.endswith("/") else "")
    parsed = urlparse(start_url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page)]
    flat = {k: v[0] for k, v in query.items()}
    return urlunparse(parsed._replace(query=urlencode(flat)))


def parse_jsonld_listings(payload: Any) -> list[dict[str, Any]]:
    """Extract listing dicts from Lamudi JSON-LD (SearchResultsPage graph)."""
    graphs: list[list[dict[str, Any]]] = []
    if isinstance(payload, list):
        for block in payload:
            if isinstance(block, dict) and isinstance(block.get("@graph"), list):
                graphs.append(block["@graph"])
    elif isinstance(payload, dict) and isinstance(payload.get("@graph"), list):
        graphs.append(payload["@graph"])

    rows: list[dict[str, Any]] = []
    for graph in graphs:
        for node in graph:
            if node.get("@type") != "SearchResultsPage":
                continue
            for entity in node.get("mainEntity") or []:
                if entity.get("@type") != "ItemList" or entity.get("@id") != "#listings":
                    continue
                for element in entity.get("itemListElement") or []:
                    item = element.get("item") if isinstance(element, dict) else None
                    if not isinstance(item, dict):
                        continue
                    row = _schema_item_to_row(item)
                    if row:
                        rows.append(row)
    return rows


def _schema_item_to_row(item: dict[str, Any]) -> dict[str, Any] | None:
    schema_type = str(item.get("@type") or "")
    if schema_type.lower() not in _RESIDENTIAL_TYPES:
        return None

    url = str(item.get("url") or item.get("@id") or "").strip()
    listing_id = listing_id_from_url(url)
    if not listing_id:
        return None

    title = str(item.get("name") or "").strip()
    description = str(item.get("description") or "").strip()
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    city = str(address.get("addressLocality") or "").strip()
    state = _normalize_state(str(address.get("addressRegion") or ""))
    neighborhood = _neighborhood_from_address(address)

    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    price_raw = offers.get("price") or item.get("price") or 0
    try:
        price = int(float(str(price_raw).replace(",", "")))
    except (TypeError, ValueError):
        price = 0

    floor = item.get("floorSize") if isinstance(item.get("floorSize"), dict) else {}
    m2_raw = floor.get("value")
    try:
        m2 = float(str(m2_raw)) if m2_raw is not None else None
    except (TypeError, ValueError):
        m2 = None

    geo = item.get("geo") if isinstance(item.get("geo"), dict) else {}
    lat = geo.get("latitude")
    lng = geo.get("longitude")

    return {
        "listing_id": listing_id,
        "url": url.split("?")[0],
        "title": title,
        "description": description,
        "neighborhood": neighborhood,
        "city": city,
        "state": state,
        "price": price,
        "currency": str(offers.get("priceCurrency") or "MXN"),
        "bedrooms": item.get("numberOfBedrooms"),
        "bathrooms": item.get("numberOfBathroomsTotal"),
        "area_m2": m2,
        "propertyType": _property_type(schema_type, title),
        "lat": lat,
        "lng": lng,
        "listing_mode": "sale",
    }


def parse_serp_html(html: str, *, start_url: str = "") -> list[dict[str, Any]]:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    if not match:
        return []
    payload = json.loads(match.group(1))
    rows = parse_jsonld_listings(payload)
    if start_url:
        mode = _listing_mode(start_url)
        for row in rows:
            row["listing_mode"] = mode
    return rows


def paginate_urls(start_url: str, *, max_pages: int = 20) -> list[str]:
    return [_page_url(start_url, page) for page in range(1, max_pages + 1)]
