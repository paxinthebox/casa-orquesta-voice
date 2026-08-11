"""People intent + filter contract tests."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")))

from people_intent import (  # noqa: E402
    classify_people_kind,
    implies_people_search,
    is_credit_broker_context,
    resolve_people_filters,
)
from tools.people_tools import find_brokers  # noqa: E402


def test_classify_broker_before_collaborator_for_inmobiliaria():
    assert classify_people_kind("Necesito una inmobiliaria en Cuernavaca") == "broker"


def test_classify_collaborator_for_agente_inmobiliario():
    assert classify_people_kind("Busco un agente inmobiliario en Polanco") == "collaborator"


def test_credit_broker_not_people_search():
    assert is_credit_broker_context("Broker de crédito: Banco X")
    assert classify_people_kind("Broker de crédito: Banco X") is None


def test_resolve_people_filters_drops_property_keys():
    resolved = resolve_people_filters(
        {
            "client_profile": {
                "area": "Cuernavaca",
                "state": "Morelos",
                "property_types": ["departamento", "casa"],
                "budget_mxn": 16000,
                "listing_mode": "rent",
            },
            "filters": {"type": "departamento", "price_max_mxn": 16000},
        },
        "Busco brokers en Cuernavaca",
    )
    assert resolved.get("state") == "Morelos"
    assert resolved.get("city") == "Cuernavaca"
    assert "type" not in resolved
    assert "price_max_mxn" not in resolved


def test_resolve_polanco_neighborhood():
    resolved = resolve_people_filters({}, "Brokers en Polanco CDMX")
    assert resolved.get("state") == "CDMX"
    assert resolved.get("neighborhood") == "polanco"


async def _run_brokers(args: dict):
    ctx = type("C", (), {"state": {}})()
    return await find_brokers(args, ctx)


def test_brokers_polanco_match_miguel_hidalgo_live():
    data = asyncio.run(
        _run_brokers({
            "query": "brokers Polanco",
            "state": "CDMX",
            "neighborhood": "polanco",
        }),
    )
    assert data["count"] >= 1
    cities = {r.get("city") for r in data["results"]}
    assert "Miguel Hidalgo" in cities or "Ciudad de México" in cities


def test_brokers_xochitepec_near_match_within_morelos():
    data = asyncio.run(
        _run_brokers({
            "query": "broker en Xochitepec Morelos",
            "state": "Morelos",
            "city": "Xochitepec",
        }),
    )
    assert data["count"] >= 1
    if data.get("match_quality") == "near":
        assert "location" in (data.get("relaxations") or [])
        assert all(r.get("state") == "Morelos" for r in data["results"])


def test_department_search_not_people():
    assert not implies_people_search("Busco departamento en renta en Cuernavaca")
