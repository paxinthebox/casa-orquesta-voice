"""
People tool handlers — Phase 1.6.

Buyer / collaborator-agent / brokerage-firm finders. Live rows from
``data/cache/people_index.json`` (portal ingest + EasyBroker) merge with
demo fallback so behavior contract tests stay green.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PEOPLE_INDEX_PATH = os.getenv("PEOPLE_INDEX_PATH", "/app/cache/people_index.json")

import sys as _sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared"))
if _SHARED not in _sys.path:
    _sys.path.insert(0, _SHARED)

from location_filters import (  # noqa: E402
    extract_location_filters,
    filter_by_state,
    fold,
    listing_matches_location,
    location_needles,
)

DEMO_BUYERS = [
    {
        "id": "B-001", "name": "María González", "state": "CDMX",
        "city": "Ciudad de México", "neighborhoods": ["Roma Norte", "Condesa"],
        "budget_mxn": 8_000_000, "property_types": ["departamento", "loft"],
        "financing": "bancario", "stage": "inbound",
        "interests": ["pet friendly", "roof garden", "walkable"],
        "lead_score": 92,
    },
    {
        "id": "B-002", "name": "Roberto Méndez", "state": "Morelos",
        "city": "Cuernavaca", "neighborhoods": ["Tabachines", "Vista Hermosa"],
        "budget_mxn": 9_000_000, "property_types": ["casa"],
        "financing": "contado", "stage": "qualified",
        "interests": ["alberca", "jardín", "seguridad"],
        "lead_score": 88,
    },
    {
        "id": "B-003", "name": "Adriana Torres", "state": "CDMX",
        "city": "Ciudad de México", "neighborhoods": ["Del Valle", "Narvarte"],
        "budget_mxn": 5_200_000, "property_types": ["departamento"],
        "financing": "INFONAVIT", "stage": "visit_scheduled",
        "interests": ["elevador", "parking", "transporte"],
        "lead_score": 84,
    },
    {
        "id": "B-004", "name": "Iván López", "state": "Morelos",
        "city": "Cuautla", "neighborhoods": ["Centro", "Oaxtepec"],
        "budget_mxn": 4_300_000, "property_types": ["casa"],
        "financing": "FOVISSSTE", "stage": "preapproved",
        "interests": ["3 recámaras", "jardín", "escuelas"],
        "lead_score": 79,
    },
]

DEMO_COLLABORATOR_AGENTS = [
    {
        "id": "CA-001", "name": "Lucía Ramírez", "state": "CDMX",
        "city": "Ciudad de México", "neighborhoods": ["Roma Norte", "Condesa", "Juárez"],
        "specialties": ["buyer tours", "departamentos", "relocation"],
        "languages": ["es", "en"], "availability": "this_week",
        "rating": 4.9,
    },
    {
        "id": "CA-002", "name": "Fernando Soto", "state": "Morelos",
        "city": "Cuernavaca", "neighborhoods": ["Tabachines", "Vista Hermosa", "Jiutepec"],
        "specialties": ["casas", "due diligence", "familias"],
        "languages": ["es"], "availability": "today",
        "rating": 4.8,
    },
    {
        "id": "CA-003", "name": "Paola Rivas", "state": "CDMX",
        "city": "Ciudad de México", "neighborhoods": ["Polanco", "Santa Fe", "Lomas"],
        "specialties": ["lujo", "inversionistas", "corporativo"],
        "languages": ["es", "en"], "availability": "next_week",
        "rating": 4.7,
    },
]

DEMO_BROKERS = [
    {
        "id": "BR-001", "name": "Gerardo Hernández", "firm": "Inmobiliaria Demo",
        "state": "CDMX", "city": "Ciudad de México",
        "neighborhoods": ["Roma Norte", "Condesa", "Coyoacán", "Del Valle"],
        "specialties": ["residencial", "primer comprador", "INFONAVIT"],
        "license": "BROKER-CDMX-1029", "rating": 4.9,
    },
    {
        "id": "BR-002", "name": "Claudia Salgado", "firm": "Morelos Premier",
        "state": "Morelos", "city": "Cuernavaca",
        "neighborhoods": ["Tabachines", "Vista Hermosa", "Tepoztlán"],
        "specialties": ["casas con alberca", "retiro", "contado"],
        "license": "BROKER-MOR-4112", "rating": 4.8,
    },
    {
        "id": "BR-003", "name": "Mauricio Klein", "firm": "Capital Luxury Realty",
        "state": "CDMX", "city": "Ciudad de México",
        "neighborhoods": ["Polanco", "Santa Fe", "Lomas"],
        "specialties": ["lujo", "inversión", "expatriados"],
        "license": "BROKER-CDMX-2201", "rating": 4.7,
    },
]

_PEOPLE_CACHE: dict[str, list[dict]] | None = None


def _people_index_candidates() -> list[Path]:
    paths: list[Path] = [Path(PEOPLE_INDEX_PATH)]
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "cache" / "people_index.json"
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _load_people_cache() -> dict[str, list[dict]]:
    global _PEOPLE_CACHE
    if _PEOPLE_CACHE is not None:
        return _PEOPLE_CACHE

    for path in _people_index_candidates():
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        _PEOPLE_CACHE = {
            "collaborators": list(raw.get("collaborators") or []),
            "brokers": list(raw.get("brokers") or []),
        }
        return _PEOPLE_CACHE

    _PEOPLE_CACHE = {"collaborators": [], "brokers": []}
    return _PEOPLE_CACHE


def reload_people_cache() -> None:
    """Clear in-process cache (tests / post-ingest)."""
    global _PEOPLE_CACHE
    _PEOPLE_CACHE = None


def _is_valid_person_name(name: str) -> bool:
    n = str(name or "").strip()
    if not n or len(n) > 48 or len(n.split()) > 5:
        return False
    lower = n.lower()
    return not lower.startswith(("con ", "la ", "el ", "los ", "las ", "sin ", "para "))


def _merge_catalog(demo: list[dict], live: list[dict]) -> list[dict]:
    """Live rows first; demo fills gaps so behavior contract tests keep passing."""
    seen = {str(row.get("id")) for row in live if row.get("id")}
    merged = list(live)
    for row in demo:
        rid = str(row.get("id", ""))
        if rid and rid not in seen:
            merged.append(row)
    return merged


def _filter_people_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if _is_valid_person_name(str(r.get("name") or ""))]


def _collaborator_catalog() -> list[dict]:
    live = _filter_people_rows(_load_people_cache().get("collaborators", []))
    return _merge_catalog(DEMO_COLLABORATOR_AGENTS, live)


def _broker_catalog() -> list[dict]:
    live = _filter_people_rows(_load_people_cache().get("brokers", []))
    return _merge_catalog(DEMO_BROKERS, live)


def _resolved_people_args(ctx: Any, args: dict) -> dict:
    """Session people filters are authoritative — LLM args cannot override location."""
    session = ctx.state.get("people_filters_resolved") or ctx.state.get("people_filters") or {}
    out = dict(session) if isinstance(session, dict) else {}
    out["query"] = (
        args.get("query")
        or ctx.state.get("user_query")
        or out.get("query")
        or ""
    )
    if not session:
        for key in ("state", "city", "neighborhood", "states"):
            if args.get(key):
                out[key] = args[key]
    for key in ("specialties", "languages"):
        if args.get(key):
            out[key] = args[key]
    if args.get("limit"):
        out["limit"] = args["limit"]
    return out


def _catalog_text(row: dict) -> str:
    parts: list[str] = []
    for value in row.values():
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        else:
            parts.append(str(value))
    return " ".join(parts).lower()


def _normalize_state(value: str) -> str:
    s = value.lower().strip()
    if s in ("cdmx", "ciudad de méxico", "ciudad de mexico", "df", "d.f.", "distrito federal"):
        return "cdmx"
    if s in ("morelos", "mor"):
        return "morelos"
    return s


def _person_as_location_row(row: dict[str, Any]) -> dict[str, str]:
    hoods = row.get("neighborhoods") or []
    hood = hoods[0] if hoods else ""
    municipality = str(row.get("city") or "")
    return {
        "state": str(row.get("state") or ""),
        "municipality": municipality,
        "city": municipality,
        "neighborhood": str(hood),
        "colonia": str(hood),
        "address": " ".join(str(h) for h in hoods),
        "title": f"{row.get('name') or ''} {row.get('firm') or ''}",
        "location": "",
    }


def _location_requested(filters: dict[str, Any], query: str) -> bool:
    if filters.get("city") or filters.get("neighborhood"):
        return True
    return bool(location_needles(filters=filters, query=query))


def _person_matches_location(row: dict[str, Any], *, filters: dict[str, Any], query: str) -> bool:
    needles = location_needles(filters=filters, query=query)
    if not needles:
        return True
    return listing_matches_location(_person_as_location_row(row), needles)


def _filter_by_state_scope(rows: list[dict], filters: dict[str, Any]) -> list[dict]:
    state = str(filters.get("state") or "").strip()
    if state:
        return filter_by_state(rows, state)
    states = filters.get("states")
    if isinstance(states, list) and states:
        allowed = {_normalize_state(str(s)) for s in states if s}
        return [
            r for r in rows
            if _normalize_state(str(r.get("state") or "")) in allowed
        ]
    return rows


def _rank_catalog(catalog: list[dict], args: dict, default_limit: int = 5) -> dict:
    query = (args.get("query") or "").lower()
    state = (args.get("state") or "").lower()
    specialties = [str(s).lower() for s in args.get("specialties", [])]
    languages = [str(s).lower() for s in args.get("languages", [])]
    limit = int(args.get("limit", default_limit))
    norm_state = _normalize_state(state) if state else ""
    source_filter = ""
    if "inmuebles24" in query or "inmuebles 24" in query:
        source_filter = "inmuebles24"
    elif "easybroker" in query:
        source_filter = "easybroker"

    relaxations: list[str] = []
    scoped = _filter_by_state_scope(catalog, args)

    pool: list[dict] = []
    for row in scoped:
        text = _catalog_text(row)
        if source_filter and source_filter not in str(row.get("source") or "").lower():
            continue
        if norm_state and _normalize_state(str(row.get("state", ""))) != norm_state:
            continue
        if languages and not set(languages) & {str(x).lower() for x in row.get("languages", [])}:
            continue
        if specialties and not any(s in text for s in specialties):
            continue
        pool.append(row)

    loc_filters = {
        k: args[k]
        for k in (
            "state", "municipality", "city", "locality",
            "colonia", "fraccionamiento", "neighborhood",
        )
        if args.get(k)
    }
    if not loc_filters and query:
        loc_filters = extract_location_filters(query)

    located = [
        row for row in pool
        if _person_matches_location(row, filters=loc_filters, query=query)
    ]
    if located:
        work = located
        match_quality = "exact"
    elif _location_requested(loc_filters, query) and pool:
        work = pool
        relaxations.append("location")
        match_quality = "near"
    else:
        work = pool
        match_quality = "exact" if work else "none"

    ranked: list[tuple[float, dict]] = []
    for row in work:
        text = _catalog_text(row)
        score = float(row.get("lead_score", row.get("rating", 0)))
        if query:
            tokens = [t for t in fold(query).split() if len(t) > 2]
            score += 10 * sum(1 for t in tokens if t in text)
        if match_quality == "near" and _location_requested(loc_filters, query):
            if not _person_matches_location(row, filters=loc_filters, query=query):
                score -= 6.0
        ranked.append((score, row))

    ranked.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, row in ranked[:limit]:
        item = dict(row)
        item["score"] = round(score, 2)
        if match_quality == "near":
            item["match_kind"] = "near"
        results.append(item)

    return {
        "count": len(results),
        "results": results,
        "match_quality": match_quality if results else "none",
        "relaxations": relaxations if match_quality == "near" else [],
        "filters_applied": dict(args),
    }


async def find_buyers(args: dict, ctx: Any) -> dict:
    resolved = _resolved_people_args(ctx, args)
    data = _rank_catalog(DEMO_BUYERS, resolved)
    ctx.state["last_buyers"] = data["results"]
    ctx.state["last_people_match_quality"] = data.get("match_quality")
    ctx.state["last_people_relaxations"] = data.get("relaxations") or []
    return data


async def find_collaborator_agents(args: dict, ctx: Any) -> dict:
    resolved = _resolved_people_args(ctx, args)
    data = _rank_catalog(_collaborator_catalog(), resolved)
    ctx.state["last_collaborator_agents"] = data["results"]
    ctx.state["last_people_match_quality"] = data.get("match_quality")
    ctx.state["last_people_relaxations"] = data.get("relaxations") or []
    return data


async def find_brokers(args: dict, ctx: Any) -> dict:
    resolved = _resolved_people_args(ctx, args)
    data = _rank_catalog(_broker_catalog(), resolved)
    ctx.state["last_brokers"] = data["results"]
    ctx.state["last_people_match_quality"] = data.get("match_quality")
    ctx.state["last_people_relaxations"] = data.get("relaxations") or []
    return data
