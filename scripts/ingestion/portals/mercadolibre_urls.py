"""Build inmuebles.mercadolibre.com.mx search URLs for Apify actors."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_BASE = "https://inmuebles.mercadolibre.com.mx"

_CDMX_REGION = "distrito-federal"
_MORELOS_REGION = "morelos"

_PROP_SEGMENT = {
    "departamentos": "departamentos",
    "departamento": "departamentos",
    "depto": "departamentos",
    "depa": "departamentos",
    "casas": "casas",
    "casa": "casas",
    "terrenos": "terrenos-lotes",
    "terreno": "terrenos-lotes",
}

_TXN_SEGMENT = {
    "venta": "venta",
    "sale": "venta",
    "renta": "renta",
    "rent": "renta",
    "alquiler": "renta",
}

_CDMX_GENERIC = frozenset({
    "ciudad de mexico",
    "cdmx",
    "df",
    "distrito federal",
})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    folded = _fold(text)
    folded = re.sub(r"[^a-z0-9]+", "-", folded)
    return folded.strip("-")


def _colonia_slug(query: dict[str, Any]) -> str:
    override = str(
        query.get("mercadolibreSlug")
        or query.get("mercadolibre_slug")
        or ""
    ).strip()
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


def _city_slug(query: dict[str, Any]) -> str:
    override = str(
        query.get("mercadolibreCitySlug")
        or query.get("mercadolibre_city_slug")
        or ""
    ).strip()
    if override:
        return _slug(override)
    city = str(query.get("city") or query.get("municipality") or "").strip()
    if _fold(city) in _CDMX_GENERIC:
        return ""
    return _slug(city)


def _is_cdmx(query: dict[str, Any]) -> bool:
    state = _fold(str(query.get("state") or "CDMX"))
    if state in {"cdmx", "ciudad de mexico", "df", "distrito federal"}:
        return True
    return _fold(str(query.get("city") or "")) in _CDMX_GENERIC


def _is_morelos(query: dict[str, Any]) -> bool:
    return _fold(str(query.get("state") or "")) == "morelos"


def mercadolibre_search_url(query: dict[str, Any]) -> str:
    """
    Map a market query row → Mercado Libre Inmuebles list-page URL.

    CDMX boroughs use ``distrito-federal/{borough}/``; colonias add one segment.
    Morelos uses ``morelos/{municipality}/``. Override with ``mercadolibreUrl``.
    """
    override = str(
        query.get("mercadolibreUrl")
        or query.get("mercadolibre_url")
        or query.get("searchUrl")
        or query.get("startUrl")
        or ""
    ).strip()
    if override.startswith("http") and "mercadolibre" in override:
        return override.split("?")[0]

    prop = _PROP_SEGMENT.get(
        _fold(str(query.get("propertyType") or "departamentos")),
        "departamentos",
    )
    txn = _TXN_SEGMENT.get(
        _fold(str(query.get("transactionType") or "venta")),
        "venta",
    )
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    city = _city_slug(query)

    if _is_cdmx(query):
        if colonia and city:
            return f"{_BASE}/{prop}/{txn}/{_CDMX_REGION}/{city}/{_colonia_slug(query)}/"
        if city:
            return f"{_BASE}/{prop}/{txn}/{_CDMX_REGION}/{city}/"
        return f"{_BASE}/{prop}/{txn}/{_CDMX_REGION}/"

    if _is_morelos(query):
        if colonia and city:
            return f"{_BASE}/{prop}/{txn}/{_MORELOS_REGION}/{city}/{_colonia_slug(query)}/"
        if city:
            return f"{_BASE}/{prop}/{txn}/{_MORELOS_REGION}/{city}/"
        return f"{_BASE}/{prop}/{txn}/{_MORELOS_REGION}/"

    if colonia and city:
        return f"{_BASE}/{prop}/{txn}/{city}/{_colonia_slug(query)}/"
    if city:
        return f"{_BASE}/{prop}/{txn}/{city}/"
    return f"{_BASE}/{prop}/{txn}/"
