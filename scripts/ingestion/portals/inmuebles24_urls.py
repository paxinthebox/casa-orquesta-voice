"""Build Inmuebles24 search URLs for Apify actors that take startUrl / urls input."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_BASE = "https://www.inmuebles24.com"

_PROP_SLUG = {
    "departamentos": "departamentos",
    "departamento": "departamentos",
    "casas": "casas",
    "casa": "casas",
    "terrenos": "terrenos",
    "terreno": "terrenos",
    "locales": "locales-comerciales",
    "oficinas": "oficinas",
}

_TXN_SLUG = {
    "venta": "venta",
    "sale": "venta",
    "renta": "renta",
    "rent": "renta",
    "alquiler": "renta",
}

_STATE_SUFFIX = {
    "CDMX": "df",
    "Morelos": "mor",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    folded = _fold(text)
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def inmuebles24_search_url(query: dict[str, Any]) -> str:
    """
    Map a market_queries.json row → Inmuebles24 list-page URL.

    Override with ``searchUrl`` on the query when you already have a portal URL.
    """
    override = str(query.get("searchUrl") or query.get("startUrl") or "").strip()
    if override.startswith("http"):
        return override

    prop = _PROP_SLUG.get(
        _fold(str(query.get("propertyType") or "departamentos")),
        "departamentos",
    )
    txn = _TXN_SLUG.get(
        _fold(str(query.get("transactionType") or "venta")),
        "venta",
    )
    state = str(query.get("state") or "CDMX")
    city = str(query.get("city") or "Ciudad de México")
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    city_key = _fold(city)

    if colonia:
        suffix = _STATE_SUFFIX.get(state, _slug(state)[:3] or "mx")
        place = f"{_slug(colonia)}-{_slug(city)}-{suffix}"
        return f"{_BASE}/{prop}-en-{txn}-en-{place}.html"

    if city_key in {"ciudad de mexico", "cdmx", "df", "distrito federal"}:
        place = "ciudad-de-mexico-df"
    else:
        suffix = _STATE_SUFFIX.get(state, _slug(state)[:3] or "mx")
        place = f"{_slug(city)}-{suffix}"

    return f"{_BASE}/{prop}-en-{txn}-en-{place}.html"
