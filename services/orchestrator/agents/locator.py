"""
locator_agent — finds properties AND people (buyers, co-broker agents,
brokerage firms) for Casa·Orquesta.

Phase 1.6 status: handlers point at `tools/listings_tools.py` and
`tools/people_tools.py`. Simulated dispatch routes by intent keywords.
"""
from __future__ import annotations

import os

from . import Agent, Tool
from .guardrails import subagent_system
from tools.listings_tools import search_listings as _h_search_listings
from tools.listings_tools import get_listing as _h_get_listing
from tools.listings_tools import compare_listings as _h_compare_listings
from tools.people_tools import find_buyers as _h_find_buyers
from tools.people_tools import find_collaborator_agents as _h_find_collaborator_agents
from tools.people_tools import find_brokers as _h_find_brokers

MODEL_DIALOG = os.getenv("ANTHROPIC_MODEL_DIALOG", "accounts/fireworks/models/deepseek-v4-flash-0731")


# ---------------- Tool descriptors ----------------
search_listings = Tool(
    name="search_listings",
    description=(
        "Busca propiedades en venta o renta anual (departamentos, casas, "
        "condominios, lofts) aplicando filtros estructurados "
        "(state, listing_mode, beds_min, price_max_mxn, type, features) "
        "y un query libre."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "filters": {
                "type": "object",
                "description": (
                    "state ('CDMX'|'Morelos'), listing_mode ('sale'|'rent'), "
                    "beds_min (int), price_max_mxn (int; mensual si es renta), "
                    "type (departamento|casa|condominio|loft|...), "
                    "features (array of str)"
                ),
            },
            "query": {"type": "string"},
            "k": {"type": "integer", "default": 8},
        },
        "required": [],
    },
    handler=_h_search_listings,
)

get_listing = Tool(
    name="get_listing",
    description="Obtiene los detalles completos de un listing por ID.",
    input_schema={
        "type": "object",
        "properties": {"listing_id": {"type": "string"}},
        "required": ["listing_id"],
    },
    handler=_h_get_listing,
)

compare_listings = Tool(
    name="compare_listings",
    description=(
        "Compara 2-4 propiedades por precio, m², ubicación, y devuelve las "
        "más baratas y más grandes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "listing_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["listing_ids"],
    },
    handler=_h_compare_listings,
)

find_buyers = Tool(
    name="find_buyers",
    description=(
        "Busca compradores por zona, presupuesto, etapa, intereses o tipo "
        "de propiedad."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "state": {"type": "string", "description": "CDMX o Morelos"},
            "city": {"type": "string"},
            "neighborhood": {"type": "string"},
            "specialties": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Intereses, financiamiento o tipo de propiedad.",
            },
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    handler=_h_find_buyers,
)

find_collaborator_agents = Tool(
    name="find_collaborator_agents",
    description=(
        "Busca agentes colaboradores por zona, especialidad, idioma y "
        "disponibilidad."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "state": {"type": "string", "description": "CDMX o Morelos"},
            "city": {"type": "string"},
            "neighborhood": {"type": "string"},
            "specialties": {"type": "array", "items": {"type": "string"}},
            "languages": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    handler=_h_find_collaborator_agents,
)

find_brokers = Tool(
    name="find_brokers",
    description=(
        "Busca brokers por zona, firma, especialidad y perfil de operación."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "state": {"type": "string", "description": "CDMX o Morelos"},
            "city": {"type": "string"},
            "neighborhood": {"type": "string"},
            "specialties": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "default": 5},
        },
        "required": [],
    },
    handler=_h_find_brokers,
)


# ---------------- Agent definition ----------------
locator_agent: Agent = Agent(
    name="locator_agent",
    description=(
        "Encuentra y rankea propiedades, compradores, agentes colaboradores y "
        "brokers. Devuelve top-K con justificación."
    ),
    system=subagent_system(
        "Eres locator_agent dentro de Casa·Orquesta. Tu trabajo es encontrar "
        "las MEJORES propiedades residenciales (departamentos, casas, "
        "condominios, lofts), compradores, agentes colaboradores y brokers "
        "en CDMX y Morelos. "
        "Para propiedades usa search_listings, get_listing o compare_listings. "
        "search_listings cubre venta (listing_mode=sale) y renta anual "
        "(listing_mode=rent); no ofrezcas renta vacacional ni Airbnb. "
        "Para compradores usa find_buyers; para agentes colaboradores usa "
        "find_collaborator_agents; para brokers usa find_brokers. "
        "Cuando termines, responde en español de México con un breve resumen "
        "y los IDs recomendados. Sé conciso: máximo 3 oraciones."
    ),
    tools=[
        search_listings, get_listing, compare_listings,
        find_buyers, find_collaborator_agents, find_brokers,
    ],
    model=MODEL_DIALOG,
)


from people_intent import classify_people_kind, resolve_people_filters  # noqa: E402


async def _locator_simulated(self: Agent, message: str, ctx) -> dict:
    """Deterministic fallback: route to the right finder by intent keywords."""
    from reply_synth import format_listing_search_reply, format_people_search_reply

    intent_message = str(ctx.state.get("user_query") or message)
    filters = ctx.state.get("filters") or {}
    people_filters = (
        ctx.state.get("people_filters")
        or resolve_people_filters(ctx.state, intent_message)
    )
    ctx.state["people_filters"] = people_filters
    finder_args = {"query": ctx.state.get("user_query", intent_message), **people_filters}
    people_kind = classify_people_kind(intent_message)

    if people_kind == "buyer":
        ctx.emit("agent_tool", self.name, {"tool": "find_buyers", "args": finder_args})
        data = await _h_find_buyers(finder_args, ctx)
        ctx.emit("tool_result", self.name, {"tool": "find_buyers", "result": data})
        return {
            "output": format_people_search_reply(
                kind="buyer",
                filters=people_filters,
                results=data.get("results", []),
                match_quality=data.get("match_quality", "exact"),
                relaxations=data.get("relaxations") or [],
            ),
            "data": data,
        }

    if people_kind == "broker":
        ctx.emit("agent_tool", self.name, {"tool": "find_brokers", "args": finder_args})
        data = await _h_find_brokers(finder_args, ctx)
        ctx.emit("tool_result", self.name, {"tool": "find_brokers", "result": data})
        return {
            "output": format_people_search_reply(
                kind="broker",
                filters=people_filters,
                results=data.get("results", []),
                match_quality=data.get("match_quality", "exact"),
                relaxations=data.get("relaxations") or [],
            ),
            "data": data,
        }

    if people_kind == "collaborator":
        ctx.emit("agent_tool", self.name, {"tool": "find_collaborator_agents", "args": finder_args})
        data = await _h_find_collaborator_agents(finder_args, ctx)
        ctx.emit("tool_result", self.name, {"tool": "find_collaborator_agents", "result": data})
        return {
            "output": format_people_search_reply(
                kind="collaborator",
                filters=people_filters,
                results=data.get("results", []),
                match_quality=data.get("match_quality", "exact"),
                relaxations=data.get("relaxations") or [],
            ),
            "data": data,
        }

    ctx.emit("agent_tool", self.name, {"tool": "search_listings",
                                       "args": {"filters": filters,
                                                "query": ctx.state.get("user_query", message)}})
    data = await _h_search_listings(
        {
            "filters": filters,
            "query": ctx.state.get("user_query", message),
            "k": 8,
        },
        ctx,
    )
    ctx.emit("tool_result", self.name, {"tool": "search_listings", "result": data})
    results = data.get("results", [])
    delta: dict = {}
    if "last_candidates" in ctx.state:
        delta["last_candidates"] = ctx.state["last_candidates"]
    if "last_match_quality" in ctx.state:
        delta["last_match_quality"] = ctx.state["last_match_quality"]
    if "last_relaxations" in ctx.state:
        delta["last_relaxations"] = ctx.state["last_relaxations"]

    return {
        "output": format_listing_search_reply(
            filters=filters,
            results=results,
            user_query=ctx.state.get("user_query", message),
            match_quality=data.get("match_quality", "exact"),
            relaxations=data.get("relaxations") or [],
        ),
        "data": data,
        "state_delta": delta,
    }


locator_agent._run_simulated = _locator_simulated.__get__(locator_agent, Agent)  # type: ignore
