"""Weighted closeness score (0–100) for listing ↔ query alignment."""
from __future__ import annotations

import re
from typing import Any

from listing_filters import listing_matches_type
from location_filters import (
    fold,
    listing_location_blob,
    listing_matches_location,
    location_needles,
    zone_match_tokens,
)

_PARKING_PATTERNS = [
    re.compile(r"(\d+)\s*(?:lugares?\s+de\s+)?estacionamiento", re.I),
    re.compile(r"(\d+)\s*(?:coches?|autos?|vehiculos?|vehículos?)", re.I),
    re.compile(r"garage\s+para\s+(\d+)", re.I),
    re.compile(r"estacionamiento\s+para\s+(\d+)", re.I),
]

_BEDS_PATTERN = re.compile(
    r"(\d+)\s*(?:rec[aá]maras?|habitaciones?|dormitorios?)",
    re.I,
)


def _listing_blob(row: dict[str, Any]) -> str:
    feats = row.get("features") or []
    feat_text = " ".join(str(f) for f in feats) if isinstance(feats, list) else ""
    return " ".join(
        str(row.get(k) or "")
        for k in ("title", "description", "type")
    ).lower() + " " + feat_text.lower()


def _parse_parking_count(text: str) -> int | None:
    for pat in _PARKING_PATTERNS:
        match = pat.search(text)
        if match:
            return int(match.group(1))
    blob = fold(text)
    if "garage" in blob or "estacionamiento" in blob or "cochera" in blob:
        return 1
    return None


def _parse_beds_count(row: dict[str, Any]) -> int:
    beds = row.get("beds")
    if isinstance(beds, (int, float)) and int(beds) > 0:
        return int(beds)
    match = _BEDS_PATTERN.search(_listing_blob(row))
    if match:
        return int(match.group(1))
    return 0


def _parking_requested(*, filters: dict[str, Any], query: str) -> int | None:
    text = fold(f"{query} {' '.join(filters.get('features') or [])}")
    for pat in _PARKING_PATTERNS:
        match = pat.search(text)
        if match:
            return int(match.group(1))
    if any(t in text for t in ("estacionamiento", "garage", "cochera", "cajones", "cajon")):
        return 1
    return None


def _wanted_types(filters: dict[str, Any]) -> list[str]:
    types = filters.get("types")
    if isinstance(types, list) and types:
        return [str(t) for t in types if str(t).strip()]
    prop_types = filters.get("property_types")
    if isinstance(prop_types, list) and prop_types:
        return [str(t) for t in prop_types if str(t).strip()]
    want = filters.get("type")
    if want:
        return [str(want)]
    return []


def _score_location(row: dict[str, Any], *, filters: dict[str, Any], query: str) -> float:
    max_pts = 35.0
    needles = location_needles(filters=filters, query=query)
    blob = listing_location_blob(row)
    neighborhood = fold(str(filters.get("neighborhood") or ""))
    city = fold(str(filters.get("city") or ""))
    query_fold = fold(query)

    if needles and listing_matches_location(row, needles):
        if neighborhood and neighborhood in blob:
            return max_pts
        return max_pts - 5.0

    if city and city in blob:
        pts = 28.0
        zone_key = str(filters.get("zone") or "").strip()
        if zone_key:
            tokens = zone_match_tokens(zone_key)
            if tokens and any(token in blob for token in tokens):
                pts = min(max_pts, pts + 7.0)
            elif "norte" in query_fold and "norte" in blob:
                pts = min(max_pts, pts + 4.0)
            else:
                pts -= 4.0
        else:
            for hint in ("norte de cuernavaca", "norte cuernavaca"):
                if hint in query_fold:
                    tokens = zone_match_tokens("norte de cuernavaca")
                    if any(token in blob for token in tokens):
                        pts = min(max_pts, pts + 7.0)
                    else:
                        pts -= 4.0
                    break
        return pts

    state = fold(str(filters.get("state") or ""))
    row_state = fold(str(row.get("state") or ""))
    if state and row_state == state:
        return 12.0
    return 0.0


def _score_price(
    row: dict[str, Any],
    price_max: int | None,
    *,
    relaxed: bool,
) -> float:
    max_pts = 25.0
    price = row.get("price_mxn")
    if not isinstance(price, (int, float)) or int(price) <= 0:
        return 0.0
    amount = int(price)
    if not price_max or price_max <= 0:
        return max_pts * 0.5

    if amount <= price_max:
        ratio = amount / price_max
        return max_pts * (0.65 + 0.35 * ratio)

    if not relaxed:
        return 0.0

    overshoot = (amount - price_max) / price_max
    return max(0.0, max_pts * (0.55 - min(0.55, overshoot * 0.8)))


def _score_type(row: dict[str, Any], filters: dict[str, Any]) -> float:
    max_pts = 15.0
    wanted = _wanted_types(filters)
    if not wanted:
        return max_pts * 0.5
    if any(listing_matches_type(row, want) for want in wanted):
        return max_pts
    return 0.0


def _score_beds(row: dict[str, Any], beds_min: int | None, *, relaxed: bool) -> float:
    max_pts = 10.0
    if not beds_min:
        return max_pts * 0.5
    got = _parse_beds_count(row)
    if got >= beds_min:
        return max_pts
    if relaxed and got >= max(1, beds_min - 1):
        return max_pts * 0.4
    if got > 0:
        return max_pts * 0.15 * (got / beds_min)
    return 0.0


def _score_features(row: dict[str, Any], *, filters: dict[str, Any], query: str) -> float:
    max_pts = 15.0
    want_parking = _parking_requested(filters=filters, query=query)
    blob = _listing_blob(row)
    features = {fold(str(f)) for f in (row.get("features") or [])}
    filter_feats = {fold(str(f)) for f in (filters.get("features") or [])}

    if want_parking:
        got = _parse_parking_count(blob)
        if got is not None:
            if got >= want_parking:
                return max_pts
            return max_pts * (got / want_parking) * 0.6
        if "estacionamiento" in features or "garage" in fold(blob):
            return max_pts * 0.35
        return 0.0

    if filter_feats:
        blob_fold = fold(blob)
        overlap = sum(
            1 for feat in filter_feats
            if feat in blob_fold or feat in features
        )
        return max_pts * overlap / len(filter_feats)

    return max_pts * 0.4


def _score_text(query: str, row: dict[str, Any]) -> float:
    max_pts = 10.0
    if not query.strip():
        return max_pts * 0.3
    tokens = {t for t in fold(query).split() if len(t) > 2}
    if not tokens:
        return max_pts * 0.3
    blob = fold(f"{listing_location_blob(row)} {_listing_blob(row)}")
    overlap = sum(1 for token in tokens if token in blob)
    return max_pts * min(1.0, overlap / max(3, len(tokens) * 0.4))


def closeness_score(
    row: dict[str, Any],
    *,
    filters: dict[str, Any],
    query: str,
    relaxations: set[str] | None = None,
) -> float:
    """Higher = closer fit to filters + natural-language query."""
    rel = relaxations or set()
    price_relaxed = "price_max_mxn" in rel
    beds_relaxed = "beds_min" in rel
    loc_relaxed = "location" in rel
    type_relaxed = "type" in rel

    loc = _score_location(row, filters=filters, query=query)
    if loc_relaxed and loc < 28.0:
        loc = max(loc, 18.0)

    price_max_raw = filters.get("price_max_mxn")
    price_max = int(price_max_raw) if price_max_raw else None
    price = _score_price(row, price_max, relaxed=price_relaxed or not price_max)
    if price_max and not price_relaxed:
        row_price = row.get("price_mxn")
        if isinstance(row_price, (int, float)) and int(row_price) > price_max:
            price = 0.0

    typ = _score_type(row, filters)
    if type_relaxed and typ == 0.0:
        typ = 15.0 * 0.35
    beds_min = int(filters["beds_min"]) if filters.get("beds_min") else None
    beds = _score_beds(row, beds_min, relaxed=beds_relaxed)
    feats = _score_features(row, filters=filters, query=query)
    text = _score_text(query, row)

    total = loc + price + typ + beds + feats + text

    if price_relaxed and price_max:
        row_price = row.get("price_mxn")
        if isinstance(row_price, (int, float)) and int(row_price) > price_max:
            total -= 8.0
    if beds_relaxed and beds_min:
        if _parse_beds_count(row) < beds_min:
            total -= 5.0
    if loc_relaxed:
        needles = location_needles(filters=filters, query=query)
        if needles and not listing_matches_location(row, needles):
            total -= 6.0

    return round(max(0.0, min(100.0, total)), 2)
