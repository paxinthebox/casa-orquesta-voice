"""Map market ingest queries → Propiedades.com Apify actor inputs."""
from __future__ import annotations

from typing import Any

_CDMX_GENERIC = frozenset({
    "ciudad de mexico",
    "cdmx",
    "df",
    "distrito federal",
})


def propiedades_market_queries(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """All CDMX/Morelos pilot queries — borough, city, colonia, and state-wide rows."""
    out: list[dict[str, Any]] = []
    for query in queries:
        if not isinstance(query, dict):
            continue
        state = str(query.get("state") or "").strip()
        if state not in {"CDMX", "Morelos"}:
            continue
        out.append(query)
    return out


def propiedades_location(query: dict[str, Any]) -> str:
    """Location string for fatihtahta/propiedades-scraper."""
    override = str(query.get("propiedadesLocation") or "").strip()
    if override:
        return override

    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    if colonia:
        aliases = query.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                text = str(alias or "").strip()
                if not text:
                    continue
                if text.casefold() == "prado churubusco":
                    return "Prado Churubusco"
        if colonia.casefold() in {"prados churubusco", "prado churubusco"}:
            return "Prado Churubusco"
        return colonia

    city = str(query.get("city") or query.get("municipality") or "").strip()
    state = str(query.get("state") or "").strip()
    scope = str(query.get("scope") or "").strip().lower()

    if city.casefold() in _CDMX_GENERIC:
        return "Ciudad de México"
    if scope == "state_wide" and state == "Morelos":
        return "Morelos"
    if city:
        return city
    if state == "CDMX":
        return "Ciudad de México"
    if state == "Morelos":
        return "Morelos"
    return ""


def propiedades_deal_type(query: dict[str, Any]) -> str:
    txn = str(query.get("transactionType") or "venta").strip().lower()
    if txn in {"renta", "rent", "rental", "alquiler"}:
        return "rent"
    return "sale"


def propiedades_property_type(query: dict[str, Any]) -> str:
    raw = str(query.get("propertyType") or "casas").strip().lower()
    if raw in {"departamentos", "departamento", "depto", "depa", "apartment"}:
        return "apartment"
    if raw in {"terrenos", "terreno", "land"}:
        return "land"
    if raw in {"oficinas", "oficina", "office"}:
        return "office"
    return "house"


def propiedades_search_label(query: dict[str, Any]) -> str:
    location = propiedades_location(query)
    return (
        f"{location} / {propiedades_property_type(query)} / "
        f"{propiedades_deal_type(query)}"
    )
