"""
Resolve people-search filters and intent — parallel to property search_intent.

People finders use location + query only; property type/budget from session are ignored.
"""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from location_filters import extract_location_filters  # noqa: E402

from client_profile import client_profile_to_filters  # noqa: E402

_BUYER_KEYWORDS = frozenset({
    "comprador", "compradores", "buyer", "buyers", "lead", "leads", "prospecto", "prospectos",
})

_BROKER_KEYWORDS = frozenset({
    "broker", "brokers", "corredor", "corredores",
    "agencia inmobiliaria", "oficina inmobiliaria", "inmobiliaria",
    "agencia", "firma inmobiliaria",
})

_COLLABORATOR_KEYWORDS = frozenset({
    "agente colaborador", "agentes colaboradores", "asesor colaborador", "asesores colaboradores",
    "colab", "colaborador", "colaboradores",
    "agente inmobiliario", "agentes inmobiliarios",
    "asesor inmobiliario", "asesores inmobiliarios", "realtor", "realtors",
})

_CREDIT_BROKER_PHRASES = (
    "broker de crédito",
    "broker de credito",
    "seguimiento crediticio",
    "convenio del consumidor",
)


def is_credit_broker_context(message: str) -> bool:
    msg = message.lower()
    return any(p in msg for p in _CREDIT_BROKER_PHRASES)


def classify_people_kind(message: str) -> str | None:
    """
    Return ``buyer`` | ``broker`` | ``collaborator`` when the turn is a people search.

    Broker (agency) is checked before collaborator when both could match
    (e.g. ``inmobiliaria en Cuernavaca``).
    """
    if is_credit_broker_context(message):
        return None
    msg = message.lower()
    if any(k in msg for k in _BUYER_KEYWORDS):
        return "buyer"
    if any(k in msg for k in _BROKER_KEYWORDS):
        return "broker"
    if any(k in msg for k in _COLLABORATOR_KEYWORDS):
        return "collaborator"
    return None


def implies_people_search(message: str) -> bool:
    return classify_people_kind(message) is not None


def resolve_people_filters(state: dict, message: str) -> dict:
    """
    Location scope for people finders — message parse + client profile area.

    Never carries listing ``type``, ``price_max_mxn``, ``beds_min``, etc.
    """
    parsed = extract_location_filters(message)
    profile = client_profile_to_filters(state.get("client_profile") or {})
    out: dict = {"query": message.strip()}

    for key in (
        "state", "municipality", "city", "locality",
        "colonia", "fraccionamiento", "neighborhood", "states",
    ):
        if parsed.get(key):
            out[key] = parsed[key]

    if not out.get("state") and not out.get("states"):
        if profile.get("state"):
            out["state"] = profile["state"]
        elif profile.get("states"):
            out["states"] = profile["states"]

    if not out.get("city") and not out.get("neighborhood"):
        area = str(profile.get("area") or "").strip()
        if area:
            area_loc = extract_location_filters(area)
            for key in (
                "state", "municipality", "city", "locality",
                "colonia", "fraccionamiento", "neighborhood",
            ):
                if area_loc.get(key) and key not in out:
                    out[key] = area_loc[key]

    if parsed.get("city") or parsed.get("neighborhood") or parsed.get("municipality"):
        out.pop("states", None)
        if parsed.get("state"):
            out["state"] = parsed["state"]

    return out
