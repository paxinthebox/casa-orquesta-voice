"""Geographic validation for CDMX + Morelos market ingest."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from .normalize import normalize_state

_MARKET_STATES = frozenset({"CDMX", "Morelos"})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


# Pilot municipalities — used for ingest queries and geo validation.
MORELOS_MARKET_CITIES: tuple[str, ...] = (
    "Cuernavaca",
    "Jiutepec",
    "Temixco",
    "Cuautla",
    "Emiliano Zapata",
    "Xochitepec",
    "Yautepec",
    "Tepoztlán",
    "Jojutla",
    "Huitzilac",
    "Tlaquiltenango",
    "Mazatepec",
    "Puente de Ixtla",
    "Ayala",
    "Atlatlahucan",
    "Zacatepec",
    "Jantetelco",
    "Tlayacapan",
    "Totolapan",
    "Tetecala",
    "Miacatlán",
    "Coatlán del Río",
    "Tetela del Volcán",
)

CDMX_MARKET_BOROUGHS: tuple[str, ...] = (
    "Álvaro Obregón",
    "Azcapotzalco",
    "Benito Juárez",
    "Coyoacán",
    "Cuajimalpa",
    "Cuauhtémoc",
    "Gustavo A. Madero",
    "Iztacalco",
    "Iztapalapa",
    "La Magdalena Contreras",
    "Miguel Hidalgo",
    "Milpa Alta",
    "Tláhuac",
    "Tlalpan",
    "Venustiano Carranza",
    "Xochimilco",
)

_MORELOS_CITIES = frozenset(_fold(c) for c in MORELOS_MARKET_CITIES)
_CDMX_BOROUGH_NAMES = frozenset(_fold(c) for c in CDMX_MARKET_BOROUGHS)
_NON_CDMX_CITIES = frozenset({
    "querétaro", "queretaro", "cancún", "cancun", "solidaridad", "playa del carmen",
    "tijuana", "guadalajara", "zapopan", "monterrey", "merida", "mérida", "puebla",
    "leon", "león", "toluca", "acapulco", "los cabos", "cabo san lucas", "veracruz",
    "oaxaca", "mazatlán", "mazatlan", "puerto vallarta", "tulum",
    "boca del rio", "boca del río", "zibata", "zibatá", "juriquilla",
})

_CDMX_GENERIC_CITIES = frozenset({
    "ciudad de mexico", "cdmx", "df", "distrito federal",
})

_CDMX_BOROUGHS = frozenset({
    "alvaro obregon", "álvaro obregón", "azcapotzalco", "benito juarez", "benito juárez",
    "coyoacan", "coyoacán", "cuajimalpa", "cuauhtemoc", "cuauhtémoc", "gustavo a. madero",
    "iztacalco", "iztapalapa", "magdalena contreras", "miguel hidalgo", "milpa alta",
    "tlahuac", "tláhuac", "tlalpan", "venustiano carranza", "xochimilco",
    "ciudad de mexico", "ciudad de méxico", "cdmx", "df", "distrito federal",
})

_CITY_STATE_HINTS: dict[str, str] = {
    "querétaro": "Querétaro",
    "queretaro": "Querétaro",
    "cuernavaca": "Morelos",
    "jiutepec": "Morelos",
    "temixco": "Morelos",
    "cuautla": "Morelos",
    "yautepec": "Morelos",
    "tepoztlan": "Morelos",
    "tepoztlán": "Morelos",
    "jojutla": "Morelos",
    "xochitepec": "Morelos",
    "emiliano zapata": "Morelos",
    "huitzilac": "Morelos",
    "tlaquiltenango": "Morelos",
    "mazatepec": "Morelos",
    "puente de ixtla": "Morelos",
    "ayala": "Morelos",
    "atlatlahucan": "Morelos",
    "zacatepec": "Morelos",
    "jantetelco": "Morelos",
    "tlayacapan": "Morelos",
    "totolapan": "Morelos",
    "tetecala": "Morelos",
    "miacatlán": "Morelos",
    "miacatlan": "Morelos",
    "coatlán del río": "Morelos",
    "coatlan del rio": "Morelos",
    "tetela del volcán": "Morelos",
    "tetela del volcan": "Morelos",
    "cancún": "Quintana Roo",
    "cancun": "Quintana Roo",
    "solidaridad": "Quintana Roo",
    "playa del carmen": "Quintana Roo",
    "guadalajara": "Jalisco",
    "zapopan": "Jalisco",
    "monterrey": "Nuevo León",
    "puebla": "Puebla",
    "tijuana": "Baja California",
}


def parse_location_string(location: str) -> tuple[str, str, str]:
    """Return neighborhood, city, state from 'Colonia, Alcaldía, CDMX'."""
    parts = [p.strip() for p in location.split(",") if p.strip()]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return parts[0], parts[1], parts[-1]


def resolve_listing_state(
    *,
    raw_state: str | None,
    city: str,
    location_text: str,
) -> str:
    for candidate in (raw_state, parse_location_string(location_text)[2]):
        normalized = normalize_state(candidate)
        if normalized:
            return normalized
    city_key = _fold(city)
    if city_key in _CITY_STATE_HINTS:
        hinted = normalize_state(_CITY_STATE_HINTS[city_key])
        if hinted:
            return hinted
    if city_key in _CDMX_BOROUGHS:
        return "CDMX"
    if city_key in _MORELOS_CITIES:
        return "Morelos"
    return ""


def listing_in_market(item: dict[str, Any]) -> bool:
    state = str(item.get("state") or "")
    if state not in _MARKET_STATES:
        return False
    city = _fold(str(item.get("city") or ""))
    neighborhood = _fold(str(item.get("neighborhood") or ""))
    if city in _NON_CDMX_CITIES or neighborhood in _NON_CDMX_CITIES:
        return False

    if state == "CDMX":
        if city and city not in _CDMX_BOROUGHS:
            return False
        if neighborhood in _NON_CDMX_CITIES:
            return False

    if state == "Morelos":
        # Any municipality in Morelos once state is resolved; block obvious out-of-market cities.
        if city in _NON_CDMX_CITIES or neighborhood in _NON_CDMX_CITIES:
            return False
        return True

    return True


def matches_market_query(item: dict[str, Any], query: dict[str, Any]) -> bool:
    expected_state = str(query.get("state") or "CDMX")
    actual_state = str(item.get("state") or "")
    if actual_state != expected_state:
        return False
    if not listing_in_market(item):
        return False

    scope = str(query.get("scope") or "").strip().lower()
    item_city = _fold(str(item.get("city") or ""))

    if scope == "colonia":
        if expected_state == "CDMX" and item_city and item_city not in _CDMX_BOROUGHS:
            return False
        if expected_state == "Morelos" and item_city and item_city not in _MORELOS_CITIES:
            return False
        query_city = _fold(str(query.get("city") or query.get("municipality") or ""))
        if query_city and item_city:
            if item_city in _CDMX_GENERIC_CITIES and expected_state == "CDMX":
                pass
            elif query_city not in item_city and item_city not in query_city:
                return False
        return _listing_matches_colonia(item, query)

    if scope in {"state_wide", "state"}:
        if expected_state == "Morelos":
            return not item_city or item_city in _MORELOS_CITIES
        if expected_state == "CDMX":
            return not item_city or item_city in _CDMX_BOROUGHS

    query_city = _fold(str(query.get("city") or ""))
    if not query_city or not item_city:
        return True

    if expected_state == "CDMX":
        if query_city in {"ciudad de mexico", "cdmx", "df", "distrito federal"}:
            return item_city not in _NON_CDMX_CITIES
        # Portal list pages often mix boroughs — keep any in-market CDMX row.
        if item_city in _CDMX_BOROUGHS:
            return True
        return query_city in item_city or item_city in query_city

    if expected_state == "Morelos":
        if not query_city:
            return item_city in _MORELOS_CITIES
        # Portal list pages often mix municipalities — keep any in-market Morelos row.
        if item_city in _MORELOS_CITIES:
            return True
        return query_city in item_city or item_city in query_city

    return True


def _colonia_aliases(query: dict[str, Any]) -> set[str]:
    name = _fold(str(query.get("colonia") or query.get("neighborhood") or ""))
    aliases: set[str] = set()
    if name:
        aliases.add(name)
    for raw in query.get("aliases") or []:
        if raw:
            aliases.add(_fold(str(raw)))
    return aliases


def _listing_matches_colonia(item: dict[str, Any], query: dict[str, Any]) -> bool:
    aliases = _colonia_aliases(query)
    if not aliases:
        return True
    blob = _fold(
        " ".join(
            str(item.get(k) or "")
            for k in ("neighborhood", "title", "description", "address", "location")
        )
    )
    return any(alias in blob for alias in aliases)


def expand_colonia_queries(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize colonia-level rows from data/market_queries_colonias.json."""
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        colonia = str(row.get("colonia") or row.get("neighborhood") or "").strip()
        if not colonia:
            continue
        out.append({**row, "colonia": colonia, "scope": "colonia"})
    return out


def expand_market_queries(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand pilot regions into per-municipality / per-borough Apify queries."""
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        state = str(row.get("state") or "")
        scope = str(row.get("scope") or "").strip().lower()
        if scope in {"state_wide"}:
            out.append({**row, "scope": "state_wide"})
            continue
        if state == "CDMX" and scope in {"boroughs", "cdmx"}:
            for borough in CDMX_MARKET_BOROUGHS:
                out.append({**row, "city": borough, "scope": "borough"})
            continue
        if state == "Morelos" and scope == "state":
            for mor_city in MORELOS_MARKET_CITIES:
                out.append({**row, "city": mor_city, "scope": "city"})
            continue
        out.append(row)
    return out


def short_title(text: str, *, max_len: int = 96) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[:max_len].rsplit(" ", 1)[0]
    return f"{cut}…"


def compact_address(*, neighborhood: str, city: str, state: str) -> str:
    parts = [p.strip() for p in (neighborhood, city, state) if p and p.strip()]
    return ", ".join(parts)


def headline_from_parts(
    *,
    raw_title: str,
    neighborhood: str,
    city: str,
    prop_type: str,
) -> str:
    label = {
        "departamento": "Departamento",
        "casa": "Casa",
        "loft": "Loft",
        "penthouse": "Penthouse",
        "terreno": "Terreno",
    }.get(prop_type, "Propiedad")
    lower = raw_title.lower().strip()
    if prop_type == "departamento" and lower.startswith("casa"):
        rest = re.sub(r"^\s*casa\s+en\s+", "", raw_title, flags=re.I).strip()
        if rest:
            return short_title(f"{label} en {rest}", max_len=100)
    if prop_type == "casa" and lower.startswith("departamento"):
        rest = re.sub(r"^\s*departamento\s+en\s+", "", raw_title, flags=re.I).strip()
        if rest:
            return short_title(f"{label} en {rest}", max_len=100)
    if len(raw_title) <= 100 and neighborhood.lower() not in raw_title.lower()[:40]:
        return short_title(raw_title, max_len=100)
    place = neighborhood or city
    if place:
        return f"{label} en {place}" + (f", {city}" if neighborhood and city and neighborhood != city else "")
    return short_title(raw_title, max_len=100)
