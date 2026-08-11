"""Progressive filter relaxation when strict listing search returns nothing."""
from __future__ import annotations

import os
import re
from typing import Any

from financing import apply_financing_filter  # noqa: E402
from listing_filters import (  # noqa: E402
    apply_listing_mode_filter,
    apply_type_filter,
    apply_types_filter,
)
from location_filters import (  # noqa: E402
    extract_location_filters,
    filter_by_location,
    filter_by_state,
    filters_without_zone_locality,
    fold,
    location_needles,
    municipality_requested,
    row_in_locality_scope,
    _specific_neighborhood_requested,
)

_BEDS_INFER_RE = re.compile(
    r"(\d+)\s*(?:rec[aá]maras?|habitaciones?|dormitorios?|beds?|bedrooms?)",
    re.I,
)
_BATHS_INFER_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:baños?|baths?|bathrooms?)",
    re.I,
)


def _within_price(row: dict[str, Any], price_max: int) -> bool:
    price = row.get("price_mxn")
    return isinstance(price, (int, float)) and 0 < int(price) <= price_max


def _text_blob(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in ("title", "description", "address")
    )


def _effective_beds(row: dict[str, Any]) -> int | None:
    """Known beds, else infer from title. None when still unknown."""
    beds = row.get("beds") or row.get("bedrooms")
    if isinstance(beds, (int, float)) and int(beds) > 0:
        return int(beds)
    match = _BEDS_INFER_RE.search(_text_blob(row))
    if match:
        return int(match.group(1))
    return None


def _effective_baths(row: dict[str, Any]) -> float | None:
    """Known baths, else infer from title. None when still unknown."""
    baths = row.get("baths") or row.get("bathrooms")
    if isinstance(baths, (int, float)) and float(baths) > 0:
        return float(baths)
    match = _BATHS_INFER_RE.search(_text_blob(row))
    if match:
        return float(match.group(1).replace(",", "."))
    return None


def _beds_ok(row: dict[str, Any], beds_min: int) -> bool:
    """Meet beds_min when known or title-inferred; truly unknown still passes."""
    beds = _effective_beds(row)
    if beds is None:
        return True
    return beds >= beds_min


def _baths_ok(row: dict[str, Any], baths_min: int) -> bool:
    """Meet baths_min when known or title-inferred; truly unknown still passes."""
    baths = _effective_baths(row)
    if baths is None:
        return True
    return baths >= baths_min


def _location_requested(filters: dict[str, Any], query: str) -> bool:
    if filters.get("city") or filters.get("neighborhood") or filters.get("zone"):
        return True
    return bool(location_needles(filters=filters, query=query))


def _zone_municipio_widen(
    base: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
    query: str,
    zone_located: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """
    Zone token matches often only one portal's metadata (e.g. Lamudi).

    Widen to the full municipio so MercadoLibre, Inmuebles24, etc. can surface
    while closeness still boosts norte / zone hits.
    """
    zone_key = str(filters.get("zone") or "").strip()
    if not zone_key:
        return zone_located, False
    municipio_filters = filters_without_zone_locality(filters)
    municipio_located = filter_by_location(
        base, filters=municipio_filters, query=query,
    )
    if not municipio_located:
        return zone_located, False
    zone_sources = {str(r.get("source") or "") for r in zone_located if r.get("source")}
    muni_sources = {str(r.get("source") or "") for r in municipio_located if r.get("source")}
    if len(muni_sources) > len(zone_sources) or len(municipio_located) > len(zone_located):
        return municipio_located, True
    return zone_located, False


def _type_locked(filters: dict[str, Any], query: str) -> bool:
    """True when the user explicitly named a property type — never widen type."""
    msg = query.lower()
    want = str(filters.get("type") or "").strip().lower()
    if want == "casa":
        return bool(re.search(r"\b(casa|house|home)\b", msg))
    if want == "departamento":
        return any(
            w in msg
            for w in (
                "departamento", "depa", "depto", "apartamento",
                "apartment", "apartments", "condo",
            )
        )
    if want == "condominio":
        return any(w in msg for w in ("condominio", "condo", "townhouse"))
    return bool(want)


def _scoped_pilot_states(
    pilot_states: list[str] | None,
    *,
    filters: dict[str, Any],
    query: str,
) -> list[str] | None:
    """Drop CDMX from dual-state pilot when the user named a Morelos city."""
    if not isinstance(pilot_states, list) or len(pilot_states) <= 1:
        return pilot_states
    scope = extract_location_filters(query)
    state = filters.get("state") or scope.get("state")
    city = filters.get("city") or scope.get("city")
    msg = fold(query)
    if state == "Morelos" or city == "Cuernavaca" or "cuernavaca" in msg:
        if not any(t in msg for t in ("cdmx", "ciudad de mexico", "ciudad de méxico", " df")):
            return ["Morelos"]
    return pilot_states


def apply_state_and_mode(
    candidates: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
    pilot_states: list[str] | None,
    skip_type: bool = False,
) -> list[dict[str, Any]]:
    """State + listing_mode + type — shared pool before location/price/beds."""
    rows = list(candidates)
    if isinstance(pilot_states, list) and pilot_states:
        allowed = {str(s) for s in pilot_states if s}
        rows = [c for c in rows if str(c.get("state") or "") in allowed]
    elif filters.get("state"):
        rows = filter_by_state(rows, str(filters["state"]))
    rows = apply_listing_mode_filter(rows, filters.get("listing_mode"))
    if not skip_type:
        if filters.get("types"):
            rows = apply_types_filter(rows, list(filters["types"]))
        elif filters.get("type"):
            rows = apply_type_filter(rows, str(filters["type"]))
    return rows


def apply_strict_filters(
    candidates: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
    query: str,
    pilot_states: list[str] | None,
) -> list[dict[str, Any]]:
    """All hard filters including location, price, beds, and mortgage."""
    rows = apply_state_and_mode(
        candidates, filters=filters, pilot_states=pilot_states,
    )
    rows = filter_by_location(rows, filters=filters, query=query)
    if filters.get("price_max_mxn"):
        rows = [
            c for c in rows
            if _within_price(c, int(filters["price_max_mxn"]))
        ]
    if filters.get("beds_min"):
        rows = [c for c in rows if _beds_ok(c, int(filters["beds_min"]))]
    if filters.get("baths_min"):
        rows = [c for c in rows if _baths_ok(c, int(filters["baths_min"]))]
    if filters.get("mortgage"):
        rows = apply_financing_filter(rows, str(filters["mortgage"]))
    return rows


def find_near_matches(
    candidates: list[dict[str, Any]],
    *,
    filters: dict[str, Any],
    query: str,
    pilot_states: list[str] | None,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    When strict filters miss, widen location then budget then beds.

    Returns (candidates, relaxations) where relaxations names which criteria
    were softened (e.g. price_max_mxn, location).
    """
    relaxations: list[str] = []
    pilot_states = _scoped_pilot_states(
        pilot_states, filters=filters, query=query,
    )
    base = apply_state_and_mode(
        candidates, filters=filters, pilot_states=pilot_states,
    )
    if not base:
        return [], relaxations

    located = filter_by_location(base, filters=filters, query=query)
    work, zone_widened = _zone_municipio_widen(
        base, filters=filters, query=query, zone_located=located,
    )
    if zone_widened:
        relaxations.append("zone")
    elif located:
        work = located
    elif _location_requested(filters, query) and (
        filters.get("type") or filters.get("types")
    ):
        if _type_locked(filters, query) and _specific_neighborhood_requested(filters):
            return [], relaxations
        # Same municipality first: soften type before widening geography.
        loose = apply_state_and_mode(
            candidates,
            filters=filters,
            pilot_states=pilot_states,
            skip_type=True,
        )
        local_any_type = filter_by_location(loose, filters=filters, query=query)
        if local_any_type:
            work = local_any_type
            if not (
                _type_locked(filters, query)
                and _specific_neighborhood_requested(filters)
            ):
                relaxations.append("type")
        elif _specific_neighborhood_requested(filters) or municipality_requested(
            filters, query,
        ):
            return [], relaxations
        else:
            scope = extract_location_filters(query)
            state = str(filters.get("state") or scope.get("state") or "").strip()
            if state:
                work = filter_by_state(base, state)
            else:
                work = list(base)
            relaxations.append("location")
    else:
        if _location_requested(filters, query) and municipality_requested(
            filters, query,
        ) and not located:
            return [], relaxations
        scope = extract_location_filters(query)
        state = str(filters.get("state") or scope.get("state") or "").strip()
        if state:
            work = filter_by_state(base, state)
        else:
            work = list(base)
        if _location_requested(filters, query):
            relaxations.append("location")

    price_max = filters.get("price_max_mxn")
    if price_max:
        cap = int(price_max)
        factor = float(os.getenv("MATCHING_NEAR_PRICE_FACTOR", "1.5"))
        ceiling = int(cap * factor)
        want = limit

        def _price_dist(row: dict[str, Any]) -> int:
            price = row.get("price_mxn")
            if not isinstance(price, (int, float)) or int(price) <= 0:
                return 10**9
            p = int(price)
            return 0 if p <= cap else p - cap

        priced = [
            c for c in work
            if isinstance(c.get("price_mxn"), (int, float)) and int(c["price_mxn"]) > 0
        ]
        strict_price = [c for c in priced if int(c["price_mxn"]) <= cap]
        ranked = sorted(priced, key=_price_dist)
        if strict_price:
            # Keep under-budget only. Do not pad with 1.5× over-budget rows —
            # that leaked into ranked cards even when exact matches existed.
            work = list(strict_price)
        else:
            relaxations.append("price_max_mxn")
            within_factor = [c for c in ranked if int(c["price_mxn"]) <= ceiling]
            # Never fall through to unbounded ranked prices — that surfaces
            # listings many× over budget when the area has no in-cap inventory.
            work = within_factor[:want] if within_factor else []

    beds_min = filters.get("beds_min")
    if beds_min:
        need = int(beds_min)
        strict_beds = [c for c in work if _beds_ok(c, need)]
        if strict_beds:
            work = strict_beds
        else:
            floor = max(1, need - 1)
            relaxed = [c for c in work if _beds_ok(c, floor)]
            if relaxed:
                work = relaxed
                relaxations.append("beds_min")

    baths_min = filters.get("baths_min")
    if baths_min:
        need_baths = int(baths_min)
        strict_baths = [c for c in work if _baths_ok(c, need_baths)]
        if strict_baths:
            work = strict_baths
        else:
            floor_b = max(1, need_baths - 1)
            relaxed_b = [
                c for c in work
                if _baths_ok(c, floor_b)
            ]
            if relaxed_b:
                work = relaxed_b
                relaxations.append("baths_min")

    if filters.get("mortgage"):
        financed = apply_financing_filter(work, str(filters["mortgage"]))
        if financed:
            work = financed
        elif work:
            relaxations.append("mortgage")

    city_widen = "location" in relaxations or "zone" in relaxations
    work = [
        row for row in work
        if row_in_locality_scope(
            row, filters=filters, query=query, allow_city_widen=city_widen,
        )
    ]

    return work, relaxations


def near_match_price_penalty(
    row: dict[str, Any],
    price_max: int,
) -> float:
    """Score adjustment: prefer listings closest to (but possibly over) budget."""
    price = row.get("price_mxn")
    if not isinstance(price, (int, float)) or int(price) <= 0:
        return -0.25
    p = int(price)
    if p <= price_max:
        return 0.20 * (p / price_max)
    overshoot = (p - price_max) / max(price_max, 1)
    return -min(0.45, 0.15 + overshoot * 0.6)
