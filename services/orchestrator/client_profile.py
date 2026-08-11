"""
Baseline search filters from a per-thread client profile (mobile formulary).
"""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from listing_filters import normalize_type_filters  # noqa: E402
from location_filters import extract_location_filters  # noqa: E402

_PILOT_STATES = frozenset({"CDMX", "Morelos"})


def client_profile_to_filters(profile: dict | None) -> dict:
    """Map client_profile session blob → locator search filters."""
    if not isinstance(profile, dict):
        return {}

    out: dict = {}

    mode = profile.get("listing_mode")
    if mode in ("sale", "rent"):
        out["listing_mode"] = mode

    budget = profile.get("budget_mxn")
    if isinstance(budget, (int, float)) and budget > 0:
        out["price_max_mxn"] = int(budget)

    state = profile.get("state")
    if state in _PILOT_STATES:
        out["state"] = state
    elif state in ("both", "CDMX y Morelos", "cdmx y morelos"):
        out["states"] = ["CDMX", "Morelos"]

    area = str(profile.get("area") or "").strip()
    if area:
        loc = extract_location_filters(area)
        out.update(loc)
        if "city" not in out and area:
            out.setdefault("neighborhood", area)

    prop_types = profile.get("property_types")
    if isinstance(prop_types, list) and prop_types:
        cleaned = [str(t).strip() for t in prop_types if str(t).strip()]
        if cleaned:
            out["property_types"] = cleaned
            if len(cleaned) == 1:
                out["type"] = cleaned[0]
            else:
                out["types"] = cleaned
    else:
        prop_type = profile.get("property_type")
        if isinstance(prop_type, str) and prop_type.strip():
            out["type"] = prop_type.strip()

    beds = profile.get("beds_min")
    if isinstance(beds, (int, float)) and beds > 0:
        out["beds_min"] = int(beds)

    baths = profile.get("baths_min")
    if isinstance(baths, (int, float)) and baths > 0:
        out["baths_min"] = int(baths)

    # loan_type is credit-follow-up context, not a listing hard filter.
    # Mapping it to mortgage zeros recall (≈97% of market rows lack financing tags).

    features = profile.get("features")
    if isinstance(features, list) and features:
        mapped: list[str] = []
        for raw in features:
            feat = str(raw).strip()
            if not feat:
                continue
            if feat.lower() in {"parking", "estacionamiento"}:
                mapped.append("estacionamiento")
            else:
                mapped.append(feat)
        if mapped:
            out["features"] = list(dict.fromkeys(mapped))

    return out


def _union_features(*lists: list | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for lst in lists:
        if not isinstance(lst, list):
            continue
        for raw in lst:
            feat = str(raw).strip()
            if not feat:
                continue
            key = feat.lower()
            if key in {"parking", "estacionamiento"}:
                feat = "estacionamiento"
                key = "estacionamiento"
            if key in seen:
                continue
            seen.add(key)
            out.append(feat)
    return out


def merge_profile_and_message_filters(state: dict, message: str, parsed_message: dict) -> dict:
    """Profile baseline first; message parse overrides on conflict."""
    baseline = client_profile_to_filters(state.get("client_profile"))
    session_filters = dict(state.get("filters") or {})
    # Formulary fields beat stale session filters left over from prior sale/rent turns.
    merged = {**session_filters, **baseline}
    if parsed_message:
        parsed = dict(parsed_message)
        msg = message.lower()
        # Profile submit prompts embed budget-analysis $ amounts and a single
        # property type token — do not let those override the formulary baseline.
        if baseline and msg.startswith("busco propiedades para un cliente"):
            if baseline.get("price_max_mxn"):
                parsed.pop("price_max_mxn", None)
            if baseline.get("types") or baseline.get("property_types"):
                parsed.pop("type", None)
            # Formulary zone/state is authoritative — prompt text also mentions
            # budget-analysis amounts and credit-broker copy that must not override.
            for key in ("state", "city", "neighborhood"):
                if baseline.get(key):
                    parsed.pop(key, None)
            # Keep formulary features; still allow prompt to add extra keywords.
            if baseline.get("features") or parsed.get("features"):
                parsed["features"] = _union_features(
                    baseline.get("features"),
                    parsed.get("features"),
                )
            if baseline.get("baths_min"):
                parsed.pop("baths_min", None)
            # Formulary prompt embeds "crédito INFONAVIT/bancario" for the
            # realtor narrative — do not hard-filter listings by that.
            parsed.pop("mortgage", None)
        # Per-thread buy/rent choice from the formulary beats incidental parse.
        if baseline.get("listing_mode"):
            parsed.pop("listing_mode", None)
        # Outside formulary submit, union features so parse never drops elevador/etc.
        if parsed.get("features") or merged.get("features"):
            parsed["features"] = _union_features(
                merged.get("features"),
                parsed.get("features"),
            )
        merged = {**merged, **parsed}
        # Voice turn named a city/colonia — do not keep wide pilot "both states".
        if parsed.get("city") or parsed.get("neighborhood") or parsed.get("municipality") or parsed.get("zone"):
            merged.pop("states", None)
            if parsed.get("state"):
                merged["state"] = parsed["state"]
            for key in (
                "municipality", "city", "locality", "zone",
                "colonia", "fraccionamiento", "neighborhood", "location_level",
            ):
                if parsed.get(key):
                    merged[key] = parsed[key]
        # Dual-type (``types``) only when this turn's parse named both; a single
        # ``type`` clears stale ``types``/``property_types`` from session/profile.
        if parsed.get("type"):
            merged.pop("types", None)
            merged.pop("property_types", None)
        elif parsed.get("types"):
            merged.pop("type", None)
            merged.pop("property_types", None)
    return normalize_type_filters(merged)