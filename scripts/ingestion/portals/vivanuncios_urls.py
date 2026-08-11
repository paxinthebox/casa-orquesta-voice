"""Build Vivanuncios search URLs for Apify actors."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_BASE = "https://www.vivanuncios.com.mx"

_PROP_SEGMENT = {
    "departamentos": "departamentos",
    "departamento": "departamentos",
    "casas": "casas",
    "casa": "casas",
    "terrenos": "terrenos",
    "terreno": "terrenos",
}

_TXN_SEGMENT = {
    "venta": "venta",
    "sale": "venta",
    "renta": "renta",
    "rent": "renta",
    "alquiler": "renta",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    folded = _fold(text)
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def _colonia_place_slug(query: dict[str, Any]) -> str:
    """Vivanuncios often uses singular colonia slugs (prado-churubusco vs prados-churubusco)."""
    override = str(query.get("vivanunciosSlug") or query.get("vivanuncios_slug") or "").strip()
    if override:
        return _slug(override)
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    aliases = [str(raw) for raw in (query.get("aliases") or []) if raw]
    if colonia.lower().startswith("prados "):
        for alias in aliases:
            if _fold(alias).startswith("prado "):
                return _slug(alias)
    for alias in aliases:
        slug = _slug(alias)
        if slug:
            return slug
    return _slug(colonia)


def vivanuncios_search_url(query: dict[str, Any]) -> str:
    """
    Map a market query row → Vivanuncios list-page URL.

    Override with ``searchUrl`` / ``vivanunciosUrl`` when you already have the portal URL.
    """
    override = str(
        query.get("vivanunciosUrl")
        or query.get("vivanuncios_url")
        or query.get("searchUrl")
        or query.get("startUrl")
        or ""
    ).strip()
    if override.startswith("http") and "vivanuncios" in override:
        return override

    prop = _PROP_SEGMENT.get(
        _fold(str(query.get("propertyType") or "departamentos")),
        "departamentos",
    )
    txn = _TXN_SEGMENT.get(
        _fold(str(query.get("transactionType") or "venta")),
        "venta",
    )
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    city = str(query.get("city") or query.get("municipality") or "").strip()

    if colonia:
        place = _colonia_place_slug(query)
        return f"{_BASE}/s-{prop}-en-{txn}/{place}/"

    if city:
        place = _slug(city)
        return f"{_BASE}/s-{prop}-en-{txn}/{place}/"

    txn_noun = "venta" if txn == "venta" else "renta"
    return f"{_BASE}/s-{txn_noun}-inmuebles/"
