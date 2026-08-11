"""Build Lamudi.com.mx search URLs for live Apify scrapers."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_BASE = "https://www.lamudi.com.mx"

_STATE_SLUG = {
    "cdmx": "distrito-federal",
    "morelos": "morelos",
}

_TXN_SUFFIX = {
    "venta": "for-sale",
    "sale": "for-sale",
    "renta": "for-rent",
    "rent": "for-rent",
    "alquiler": "for-rent",
}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    folded = _fold(text)
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def _colonia_slug(query: dict[str, Any]) -> str:
    override = str(query.get("lamudiSlug") or query.get("lamudi_slug") or "").strip()
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


def _state_slug(query: dict[str, Any]) -> str:
    override = str(query.get("lamudiStateSlug") or "").strip()
    if override:
        return _slug(override)
    state = _fold(str(query.get("state") or "CDMX"))
    return _STATE_SLUG.get(state, _slug(state))


def _city_slug(query: dict[str, Any]) -> str:
    override = str(query.get("lamudiCitySlug") or "").strip()
    if override:
        return _slug(override)
    return _slug(str(query.get("city") or query.get("municipality") or ""))


def _property_path(query: dict[str, Any]) -> str | None:
    """Return extra path segment for property type, or None for all residential types."""
    raw = _fold(str(query.get("propertyType") or "casas"))
    if raw in {"departamentos", "departamento", "depto", "depa"}:
        return "departamento/departamentos"
    if raw in {"terrenos", "terreno"}:
        return "terreno/terrenos"
    # Casas: verified colonia URLs use plain /for-sale/ (type filtered in sanitize).
    return None


def lamudi_search_url(query: dict[str, Any]) -> str:
    """
    Map a market query row → Lamudi list-page URL (canonical path segments).

    Override with ``lamudiUrl`` when you already verified the portal URL in a browser.
    Never use ``/{place}/casas/for-sale/`` — Lamudi resolves ``casas`` as the city
    *Casas* (Tamaulipas), not property type.
    """
    override = str(
        query.get("lamudiUrl")
        or query.get("lamudi_url")
        or query.get("searchUrl")
        or query.get("startUrl")
        or ""
    ).strip()
    if override.startswith("http") and "lamudi" in override:
        return override.split("?")[0]

    state = _state_slug(query)
    city = _city_slug(query)
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    prop = _property_path(query)
    txn = _transaction_suffix(query)
    prop_seg = f"{prop}/" if prop else ""

    if colonia and city:
        place = _colonia_slug(query)
        return f"{_BASE}/{state}/{city}/{place}/{prop_seg}{txn}/"

    if city:
        return f"{_BASE}/{state}/{city}/{prop_seg}{txn}/"

    if state == "distrito-federal":
        return f"{_BASE}/{state}/{prop_seg}{txn}/"

    return f"{_BASE}/{state}/{prop_seg}{txn}/"


def _transaction_suffix(query: dict[str, Any]) -> str:
    raw = _fold(str(query.get("transactionType") or "venta"))
    return _TXN_SUFFIX.get(raw, "for-sale")
