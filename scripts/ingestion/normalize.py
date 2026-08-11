"""Map third-party listing/agent records to Casa·Orquesta shapes."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .media_extract import extract_listing_media

_SHARED = Path(__file__).resolve().parents[2] / "services" / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from listing_filters import infer_canonical_type  # noqa: E402

_STATE_ALIASES: dict[str, str] = {
    "ciudad de méxico": "CDMX",
    "cdmx": "CDMX",
    "distrito federal": "CDMX",
    "df": "CDMX",
    "morelos": "Morelos",
    "mor": "Morelos",
}

_TYPE_ALIASES: dict[str, str] = {
    "departamento": "departamento",
    "departamentos": "departamento",
    "apartment": "departamento",
    "apartamento": "departamento",
    "casa": "casa",
    "casas": "casa",
    "house": "casa",
    "condominio": "condominio",
    "condominios": "condominio",
    "condo": "condominio",
    "townhouse": "condominio",
    "loft": "loft",
    "terreno": "terreno",
    "land": "terreno",
    "oficina": "oficina",
    "office": "oficina",
    "local": "local",
    "penthouse": "penthouse",
}


def normalize_state(raw: str | None) -> str:
    if not raw:
        return ""
    key = raw.strip().lower()
    if key in _STATE_ALIASES:
        return _STATE_ALIASES[key]
    if "cdmx" in key:
        return "CDMX"
    if "méxico" in key or "mexico" in key:
        return "CDMX"
    if "morelos" in key:
        return "Morelos"
    return raw.strip()


def normalize_property_type(raw: str | None) -> str:
    if not raw:
        return "inmueble"
    key = raw.strip().lower()
    return _TYPE_ALIASES.get(key, key)


def _price_from_operations(
    operations: list[dict[str, Any]] | None,
    *,
    want_mode: str,
) -> int | None:
    for op in operations or []:
        op_type = str(op.get("type", "")).lower()
        if want_mode == "sale" and op_type not in ("sale", "venta"):
            continue
        if want_mode == "rent" and op_type not in ("rent", "renta", "rental", "alquiler"):
            continue
        amount = op.get("amount")
        if amount is None:
            continue
        currency = str(op.get("currency", "MXN")).upper()
        if currency == "MXN":
            return int(amount)
        if currency == "USD":
            return int(float(amount) * 18)
    return None


def _listing_mode_from_operations(operations: list[dict[str, Any]] | None) -> str:
    for op in operations or []:
        op_type = str(op.get("type", "")).lower()
        if op_type in ("rent", "renta", "rental", "alquiler"):
            return "rent"
    return "sale"


def _sale_price_mxn(operations: list[dict[str, Any]] | None) -> int | None:
    return _price_from_operations(operations, want_mode="sale")


def easybroker_listing(raw: dict[str, Any], *, mls: bool = False) -> dict[str, Any]:
    """Normalize an EasyBroker property payload to our listings JSON shape."""
    loc = raw.get("location") or {}
    public_id = str(raw.get("public_id") or raw.get("id") or "")
    prefix = "EB-MLS" if mls else "EB"
    listing_id = f"{prefix}-{public_id}" if public_id else f"{prefix}-unknown"

    # Location.name is often "Colonia, Alcaldía, Ciudad" from detail endpoint.
    loc_name = str(loc.get("name") or "")
    loc_parts = [p.strip() for p in loc_name.split(",") if p.strip()] if loc_name else []

    state = normalize_state(loc.get("region") or loc.get("state") or (loc_parts[-1] if loc_parts else ""))
    city = str(loc.get("city") or (loc_parts[-1] if len(loc_parts) >= 2 else "") or "")
    neighborhood = str(loc.get("neighborhood") or loc.get("colony") or (loc_parts[0] if loc_parts else ""))

    street = loc.get("street") or raw.get("street")
    parts = [p for p in (street, neighborhood, city, state) if p]
    address = ", ".join(str(p) for p in parts)

    operations = raw.get("operations")
    listing_mode = _listing_mode_from_operations(operations)
    price_mxn = _price_from_operations(operations, want_mode=listing_mode)
    if price_mxn is None:
        price_mxn = raw.get("price") or raw.get("sale_price") or raw.get("rent_price") or 0

    features: list[str] = []
    for key in ("features", "amenities"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    name = item.get("name")
                    if name:
                        features.append(str(name))
                else:
                    features.append(str(item))
        elif isinstance(val, dict):
            features.extend(str(k) for k, enabled in val.items() if enabled)

    images = raw.get("images") or raw.get("property_images") or []
    media = extract_listing_media(raw)
    if not media:
        for img in images:
            if isinstance(img, str) and img.startswith("http"):
                media.append(img)
            elif isinstance(img, dict):
                url = img.get("url") or img.get("original") or img.get("thumb")
                if url:
                    media.append(str(url))

    agent = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    agent_id = raw.get("agent_id") or agent.get("id")

    eb_status = str(raw.get("status") or "published").lower()
    catalog_status = "available" if eb_status in ("published", "active", "for_sale") else "draft"

    listing: dict[str, Any] = {
        "id": listing_id,
        "title": str(raw.get("title") or raw.get("name") or "Propiedad EasyBroker"),
        "address": address,
        "state": state or "CDMX",
        "city": city,
        "neighborhood": neighborhood,
        "lat": loc.get("latitude") or loc.get("lat"),
        "lng": loc.get("longitude") or loc.get("lng"),
        "price_mxn": int(price_mxn or 0),
        "beds": int(raw.get("bedrooms") or raw.get("beds") or 0),
        "baths": float(raw.get("bathrooms") or raw.get("baths") or 0),
        "m2": float(raw.get("construction_size") or raw.get("lot_size") or raw.get("m2") or 0),
        "type": normalize_property_type(raw.get("property_type")),
        "listing_mode": listing_mode,
        "status": catalog_status,
        "features": features[:12],
        "description": str(raw.get("description") or "")[:2000],
        "source": "easybroker_mls" if mls else "easybroker",
        "source_url": raw.get("public_url") or raw.get("url"),
        "agent_id": agent_id,
        "agency_id": raw.get("agency_id"),
        "media": media,
    }
    if media:
        listing["thumbnail"] = media[0]
    if listing_mode == "rent":
        listing["rent_term"] = "long"
    if agent:
        listing["_agent"] = agent  # stripped before POST; used for people index
        agent_name = str(
            agent.get("full_name")
            or agent.get("name")
            or " ".join(filter(None, [agent.get("first_name"), agent.get("last_name")]))
            or ""
        ).strip()
        if agent_name:
            listing["agent_name"] = agent_name
    listing["type"] = infer_canonical_type(
        title=str(listing.get("title") or ""),
        description=str(listing.get("description") or ""),
        fallback=str(listing.get("type") or ""),
    )
    return listing


def easybroker_agent(raw: dict[str, Any], agency_name: str = "") -> dict[str, Any]:
    agent_id = str(raw.get("id") or raw.get("public_id") or "")
    loc = raw.get("location") or {}
    state = normalize_state(loc.get("region") or raw.get("state"))
    city = str(loc.get("city") or raw.get("city") or "")

    neighborhoods: list[str] = []
    for key in ("neighborhoods", "zones", "areas"):
        val = raw.get(key)
        if isinstance(val, list):
            neighborhoods.extend(str(x) for x in val)

    specialties: list[str] = []
    for key in ("specialties", "tags", "property_types", "title"):
        val = raw.get(key)
        if isinstance(val, list):
            specialties.extend(str(x) for x in val)
        elif isinstance(val, str) and val:
            specialties.append(val)

    langs = raw.get("languages") or ["es"]
    if isinstance(langs, str):
        langs = [langs]

    name = str(
        raw.get("full_name")
        or raw.get("name")
        or " ".join(filter(None, [raw.get("first_name"), raw.get("last_name")]))
        or "Agente"
    ).strip()
    return {
        "id": f"EB-A-{agent_id}" if agent_id else f"EB-A-{name[:12]}",
        "name": name,
        "state": state or "CDMX",
        "city": city,
        "neighborhoods": neighborhoods,
        "specialties": specialties or ["EasyBroker"],
        "languages": [str(x).lower()[:2] for x in langs],
        "availability": "this_week",
        "rating": float(raw.get("rating") or 4.6),
        "source": "easybroker",
        "firm": agency_name or raw.get("agency_name") or "",
        "source_url": raw.get("profile_url") or raw.get("public_url") or raw.get("url"),
    }


def easybroker_user(raw: dict[str, Any]) -> dict[str, Any]:
    """Organization user from GET /users → collaborator card."""
    return easybroker_agent(raw)


def easybroker_collaboration(raw: dict[str, Any]) -> dict[str, Any]:
    """MLS collaboration agency from GET /collaborations → broker card."""
    agency_id = str(raw.get("agency_id") or raw.get("id") or "")
    name = str(raw.get("agency_name") or raw.get("name") or "Agencia colaboradora")
    return {
        "id": f"EB-AG-{agency_id}" if agency_id else f"EB-AG-{name[:12]}",
        "name": name,
        "firm": name,
        "state": "CDMX",
        "city": "",
        "neighborhoods": [],
        "specialties": ["colaboración MLS", "Bolsa Inmobiliaria"],
        "license": "EasyBroker MLS",
        "rating": 4.5,
        "source": "easybroker",
        "source_url": "",
    }


def easybroker_broker(raw: dict[str, Any]) -> dict[str, Any]:
    agency_id = str(raw.get("id") or raw.get("public_id") or "")
    loc = raw.get("location") or {}
    state = normalize_state(loc.get("region") or raw.get("state"))
    city = str(loc.get("city") or raw.get("city") or "")

    neighborhoods: list[str] = []
    for key in ("neighborhoods", "zones"):
        val = raw.get(key)
        if isinstance(val, list):
            neighborhoods.extend(str(x) for x in val)

    name = str(raw.get("name") or raw.get("legal_name") or "Agencia")
    contact = raw.get("contact") or {}
    contact_name = str(contact.get("name") or name)

    return {
        "id": f"EB-AG-{agency_id}" if agency_id else f"EB-AG-{name[:12]}",
        "name": contact_name,
        "firm": name,
        "state": state or "CDMX",
        "city": city,
        "neighborhoods": neighborhoods,
        "specialties": ["residencial", "MLS EasyBroker"],
        "license": str(raw.get("license") or raw.get("registration") or "AMPI/EB"),
        "rating": float(raw.get("rating") or 4.5),
        "source": "easybroker",
        "source_url": raw.get("profile_url") or raw.get("url"),
    }
