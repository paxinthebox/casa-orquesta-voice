"""Repair portal listing rows (IDs, titles, geography)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sys

_SHARED = Path(__file__).resolve().parents[2] / "services" / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from financing import enrich_listing_financing  # noqa: E402

from .media_extract import sanitize_listing_media
from .market_geo import (
    compact_address,
    headline_from_parts,
    listing_in_market,
    parse_location_string,
    resolve_listing_state,
    short_title,
)

_POSTING_ID_RE = re.compile(r"(\d{6,})\.html")
_TRAILING_ID_RE = re.compile(r"/(\d{6,})(?:\.html)?(?:\?|$|/)")
_MLM_ID_RE = re.compile(r"MLM-?(\d{6,})", re.I)
_LAMUDI_DETALLE_RE = re.compile(r"lamudi\.com\.mx/detalle/([^/?#]+)", re.I)


def posting_id_from_url(url: str) -> str:
    if not url:
        return ""
    lamudi = _LAMUDI_DETALLE_RE.search(url)
    if lamudi:
        return lamudi.group(1)
    mlm = _MLM_ID_RE.search(url)
    if mlm:
        return mlm.group(1)
    match = _POSTING_ID_RE.search(url)
    if match:
        return match.group(1)
    trailing = _TRAILING_ID_RE.search(url)
    return trailing.group(1) if trailing else ""


def infer_property_type(
    *,
    raw_type: str,
    query_type: str = "",
    title: str = "",
    description: str = "",
    source_url: str = "",
) -> str:
    """Title/description beat portal fields and ingest query hints."""
    from listing_filters import infer_canonical_type  # noqa: E402

    return infer_canonical_type(
        title=title,
        description=description,
        fallback=raw_type or query_type,
        source_url=source_url,
    )


def sanitize_portal_listing(
    row: dict[str, Any],
    *,
    query: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize a portal row in-place; return None if outside CDMX/Morelos pilot."""
    source_url = str(row.get("source_url") or row.get("url") or "").strip()
    posting_id = posting_id_from_url(source_url)
    if not posting_id:
        lid = str(row.get("id") or "")
        for prefix in ("I24-", "VA-", "PROP-", "LAM-", "ML-"):
            if lid.startswith(prefix):
                posting_id = lid.removeprefix(prefix).split(".")[0]
                break
    if not posting_id:
        return None

    location_text = ""
    loc = row.get("location")
    if isinstance(loc, str):
        location_text = loc
    elif isinstance(loc, dict):
        location_text = str(loc.get("name") or "")

    nbhd, loc_city, _ = parse_location_string(location_text)
    neighborhood = str(row.get("neighborhood") or nbhd or "").strip()
    city = str(row.get("city") or loc_city or "").strip()
    state = resolve_listing_state(
        raw_state=str(row.get("state") or ""),
        city=city,
        location_text=location_text,
    )
    if not state and query:
        qs = str(query.get("state") or "")
        if qs in ("CDMX", "Morelos"):
            state = qs

    query_type = str((query or {}).get("propertyType") or "")
    raw_title = str(row.get("title") or "")
    prop_type = infer_property_type(
        raw_type=str(row.get("type") or row.get("property_type") or ""),
        query_type=query_type,
        title=raw_title,
        description=str(row.get("description") or ""),
        source_url=source_url,
    )

    headline = headline_from_parts(
        raw_title=raw_title,
        neighborhood=neighborhood,
        city=city,
        prop_type=prop_type,
    )
    address = compact_address(neighborhood=neighborhood, city=city, state=state)
    if not address:
        address = short_title(raw_title, max_len=80)

    description = str(row.get("description") or "").strip()
    if not description and len(raw_title) > 120:
        description = short_title(raw_title, max_len=2000)

    source = str(row.get("source") or "inmuebles24")
    id_prefix = {
        "vivanuncios": "VA-",
        "propiedades": "PROP-",
        "lamudi": "LAM-",
        "mercadolibre": "ML-",
    }.get(source, "I24-")

    cleaned: dict[str, Any] = {
        **row,
        "id": f"{id_prefix}{posting_id}",
        "title": headline,
        "address": address,
        "state": state,
        "city": city,
        "neighborhood": neighborhood,
        "type": prop_type,
        "description": description,
        "source": source,
        "source_url": source_url.split("?")[0] if source_url else "",
    }

    txn = str((query or {}).get("transactionType") or "").strip().lower()
    if txn in ("renta", "rent", "rental", "alquiler"):
        cleaned["listing_mode"] = "rent"
        cleaned["rent_term"] = "long"
    elif txn in ("venta", "sale"):
        cleaned["listing_mode"] = "sale"

    sanitize_listing_media(cleaned)
    if not listing_in_market(cleaned):
        return None
    if query and not _matches_query(cleaned, query):
        return None
    enrich_listing_financing(cleaned)
    return cleaned


def _matches_query(item: dict[str, Any], query: dict[str, Any]) -> bool:
    from .market_geo import matches_market_query

    return matches_market_query(item, query)


def sanitize_portal_batch(
    listings: list[dict[str, Any]],
    *,
    queries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Repair cached listings. When queries are unknown, keep any in-market row."""
    out: list[dict[str, Any]] = []
    for row in listings:
        if str(row.get("source") or "") != "inmuebles24" and not str(row.get("id", "")).startswith("I24-"):
            out.append(row)
            continue
        if queries:
            matched = False
            for query in queries:
                cleaned = sanitize_portal_listing(row, query=query)
                if cleaned:
                    out.append(cleaned)
                    matched = True
                    break
            if not matched:
                continue
        else:
            cleaned = sanitize_portal_listing(row, query=None)
            if cleaned:
                out.append(cleaned)
    return out
