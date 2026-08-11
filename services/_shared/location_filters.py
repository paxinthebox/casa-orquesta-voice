"""
Geographic filter extraction and strict city matching (CDMX + Morelos pilot).

Delegates hierarchy resolution to ``location_registry`` (estado → municipio →
colonia/fraccionamiento). Listings store municipio in ``city`` and colonia/frac
in ``neighborhood``.
"""
from __future__ import annotations

from location_registry import (  # noqa: E402
    LOCATION_ZONES,
    fold,
    is_specific_locality,
    location_needles_from_filters,
    resolve_location,
    zone_match_tokens,
)

# Legacy aliases for tests / imports
_CITY_HINTS = ()
_NEIGHBORHOOD_HINTS = ()

_CDMX_STATE_TOKENS = frozenset({
    "cdmx", "ciudad de mexico", "ciudad de méxico", "df", "distrito federal",
    "valle de mexico", "valle de méxico",
})

_CDMX_CITY_ALIASES = frozenset({
    "cdmx", "ciudad de mexico", "df", "distrito federal", "mexico", "méxico",
})


def _normalize_location_text(message: str) -> str:
    from location_registry import _normalize_text
    return _normalize_text(message)


def extract_location_filters(message: str) -> dict:
    """Pull state/municipio/colonia/fraccionamiento from natural-language es-MX."""
    return resolve_location(message).to_filters()


def location_needles(*, filters: dict, query: str) -> set[str]:
    """Tokens that must appear in a listing's or person's location fields."""
    return location_needles_from_filters(filters, query=query)


def listing_location_blob(row: dict) -> str:
    """
    Location text used for colonia / municipio matching.

    Deliberately omits ``description`` — portal marketing copy often name-drops
    nearby colonias ("a solo pasos de Condesa") and would falsely match.
    """
    return fold(
        " ".join(
            str(row.get(k) or "")
            for k in (
                "state",
                "municipality",
                "city",
                "locality",
                "colonia",
                "fraccionamiento",
                "neighborhood",
                "address",
                "title",
                "location",
            )
        )
    )


def listing_matches_location(row: dict, needles: set[str]) -> bool:
    if not needles:
        return True
    blob = listing_location_blob(row)
    return any(n in blob for n in needles)


def listing_matches_zone(row: dict, zone_key: str) -> bool:
    """True when a listing belongs in a directional zone (e.g. norte de Cuernavaca)."""
    spec = LOCATION_ZONES.get(zone_key)
    if not spec:
        return True
    municipality, state, tokens = spec
    blob = listing_location_blob(row)
    mun_fold = fold(municipality)
    state_fold = fold(state)
    in_market = (
        mun_fold in blob
        or state_fold in blob
        or (zone_key == "norte de cuernavaca" and fold("huitzilac") in blob)
    )
    if not in_market:
        return False
    if any(token in blob for token in tokens):
        return True
    if zone_key == "norte de cuernavaca" and "norte" in blob and "cuernavaca" in blob:
        return True
    return False


def filter_by_state(candidates: list[dict], state: str | None) -> list[dict]:
    """Hard-filter by state when the user or session named one."""
    if not state:
        return candidates
    want = fold(state)
    if want in _CDMX_STATE_TOKENS:
        want = "cdmx"
    out: list[dict] = []
    for row in candidates:
        got = fold(str(row.get("state") or ""))
        if want == "cdmx" and (got == "cdmx" or got in _CDMX_CITY_ALIASES):
            out.append(row)
        elif want == fold("Morelos") and got == fold("Morelos"):
            out.append(row)
        elif got == want:
            out.append(row)
    return out


def filter_by_location(
    candidates: list[dict],
    *,
    filters: dict,
    query: str,
) -> list[dict]:
    """
    Keep listings in the requested city/colonia/fraccionamiento.

    When the user named a place and nothing matches, return [] — never widen
    to the whole state.
    """
    zone = str(filters.get("zone") or "").strip()
    if zone:
        return [c for c in candidates if listing_matches_zone(c, zone)]

    needles = location_needles(filters=filters, query=query)
    if not needles:
        return candidates
    return [c for c in candidates if listing_matches_location(c, needles)]


def locality_scope(filters: dict, query: str) -> dict[str, str]:
    """Effective location scope for near-match suggestions."""
    scope: dict[str, str] = {}
    for key in (
        "state", "municipality", "city", "locality",
        "colonia", "fraccionamiento", "neighborhood", "zone",
    ):
        val = str(filters.get(key) or "").strip()
        if val:
            scope[key] = val
    parsed = extract_location_filters(query)
    for key in (
        "state", "municipality", "city", "locality",
        "colonia", "fraccionamiento", "neighborhood", "zone",
    ):
        if key not in scope and parsed.get(key):
            scope[key] = str(parsed[key])
    return scope


def _specific_neighborhood_requested(filters: dict) -> bool:
    return is_specific_locality(filters)


def municipality_requested(filters: dict, query: str) -> bool:
    """True when the user named a municipio/alcaldía without a specific colonia/frac."""
    scope = locality_scope(filters, query)
    if scope.get("zone"):
        return True
    if not (scope.get("municipality") or scope.get("city")):
        return False
    return not is_specific_locality(scope)


def filters_without_zone_locality(filters: dict[str, Any]) -> dict[str, Any]:
    """Municipio-level filters — drop zone/colonia so all portals in the city qualify."""
    drop = {"zone", "neighborhood", "fraccionamiento", "colonia", "locality"}
    return {k: v for k, v in filters.items() if k not in drop}


def row_in_locality_scope(
    row: dict,
    *,
    filters: dict,
    query: str,
    allow_city_widen: bool = False,
) -> bool:
    """
    True when a listing belongs in the user's locality scope.

    ``allow_city_widen`` — price/beds relaxed but still same state/municipio.
    Never widens to another state. Specific fraccionamientos never widen.
    """
    scope = locality_scope(filters, query)
    state = scope.get("state")
    if state and not filter_by_state([row], state):
        return False
    if is_specific_locality(scope) and not allow_city_widen:
        zone = str(scope.get("zone") or "").strip()
        if zone:
            return listing_matches_zone(row, zone)
        needles = location_needles(filters=scope, query=query)
        return listing_matches_location(row, needles) if needles else False
    municipality = scope.get("municipality") or scope.get("city")
    if allow_city_widen or not municipality:
        return True
    blob = listing_location_blob(row)
    mun = fold(municipality)
    if mun == fold("Ciudad de México"):
        return any(alias in blob for alias in _CDMX_CITY_ALIASES)
    return mun in blob
