"""Map market queries → Lamudi inventory export filters."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def lamudi_area_query(query: dict[str, Any]) -> str:
    override = str(query.get("lamudiArea") or query.get("lamudiLocation") or "").strip()
    if override:
        return override
    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    if colonia:
        if colonia.casefold() in {"prados churubusco", "prado churubusco"}:
            return "Prado Churubusco"
        return colonia
    city = str(query.get("city") or "").strip()
    if city:
        if _fold(city) in {"ciudad de mexico", "cdmx", "df"}:
            return "Ciudad de Mexico"
        return city
    state = str(query.get("state") or "").strip()
    if state == "Morelos":
        return "Cuernavaca"
    return "Ciudad de Mexico"


def lamudi_market(query: dict[str, Any]) -> str:
    txn = _fold(str(query.get("transactionType") or "venta"))
    if txn in {"renta", "rent", "rental", "alquiler"}:
        return "to-rent"
    return "for-sale"


def lamudi_property_type_query(query: dict[str, Any]) -> str:
    raw = _fold(str(query.get("propertyType") or "casas"))
    if raw in {"departamentos", "departamento", "depto", "depa", "apartment"}:
        return "apartment"
    if raw in {"terrenos", "terreno", "land"}:
        return "land"
    if raw in {"oficinas", "oficina", "office"}:
        return "office"
    return "house"


def lamudi_search_label(query: dict[str, Any]) -> str:
    return (
        f"{lamudi_area_query(query)} / {lamudi_property_type_query(query)} / "
        f"{lamudi_market(query)}"
    )


def lamudi_title_matches_query(title: str, query: dict[str, Any]) -> bool:
    """Inventory areaQuery is fuzzy — require parsed title geo to match the query."""
    from .lamudi_geo import parse_lamudi_title_location

    title = fix_mojibake(title)
    nbhd, city, state = parse_lamudi_title_location(title)
    expected_state = str(query.get("state") or "").strip()
    if state and expected_state and state != expected_state:
        return False
    if expected_state == "Morelos" and state and state != "Morelos":
        return False
    if expected_state == "CDMX" and state and state not in {"CDMX", ""}:
        return False

    query_city = _fold(str(query.get("city") or ""))
    parsed_city = _fold(city)
    if query_city and parsed_city and query_city not in parsed_city and parsed_city not in query_city:
        if expected_state != "CDMX" or "ciudad de mexico" not in parsed_city:
            return False

    colonia = str(query.get("colonia") or query.get("neighborhood") or "").strip()
    if colonia:
        blob = _fold(f"{nbhd} {title}")
        aliases = query.get("aliases")
        tokens = {_fold(colonia)}
        if isinstance(aliases, list):
            tokens.update(_fold(str(a)) for a in aliases if str(a).strip())
        if not any(tok and tok in blob for tok in tokens):
            return False

    if expected_state == "CDMX":
        junk_states = (
            "puebla", "yucatan", "merida", "guadalajara", "monterrey",
            "queretaro", "tijuana", "cancun", "tamaulipas",
        )
        blob = _fold(title)
        if any(f", {st}" in blob or f" {st}," in blob for st in junk_states):
            return False
    return True


def fix_mojibake(text: str) -> str:
    """Repair common Lamudi inventory UTF-8 shown as Latin-1."""
    from .lamudi_geo import fix_mojibake as _fix

    return _fix(text)
