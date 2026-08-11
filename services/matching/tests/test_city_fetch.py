"""Matching must fetch municipio inventory, not only the first N statewide rows."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(SVC, "..", "_shared"))
sys.path.insert(0, SVC)
sys.path.insert(0, SHARED)

from fetch_params import build_listings_fetch_params  # noqa: E402


def test_city_param_when_municipio_named():
    params = build_listings_fetch_params(
        {
            "type": "departamento",
            "listing_mode": "sale",
            "state": "Morelos",
            "city": "Xochitepec",
        },
        query="departamentos en venta en Xochitepec Morelos",
        candidate_limit=200,
    )
    assert params["city"] == "Xochitepec"
    assert params["state"] == "Morelos"
    # Type stays in the SQL pool so colonia/budget inventory is not starved.
    assert params["type"] == "departamento"


def test_type_param_when_no_city_in_query():
    params = build_listings_fetch_params(
        {
            "type": "departamento",
            "listing_mode": "sale",
            "state": "Morelos",
        },
        query="departamentos en venta en Morelos",
        candidate_limit=200,
    )
    assert "city" not in params
    assert params["type"] == "departamento"


def test_balance_sources_for_hybrid_candidate_pool():
    params = build_listings_fetch_params(
        {"state": "Morelos", "listing_mode": "sale"},
        query="casa en venta Cuernavaca",
        candidate_limit=800,
    )
    assert params["balance_sources"] is True
    assert params["limit"] == 800


def test_profile_filters_forward_type_and_budget_headroom():
    params = build_listings_fetch_params(
        {
            "type": "departamento",
            "listing_mode": "sale",
            "state": "CDMX",
            "municipality": "Cuauhtémoc",
            "colonia": "Condesa",
            "neighborhood": "Condesa",
            "beds_min": 2,
            "price_max_mxn": 8_000_000,
        },
        query="Busco propiedades para un cliente en Condesa",
        candidate_limit=200,
    )
    assert params["city"] == "Cuauhtémoc"
    assert params["type"] == "departamento"
    # beds stay out of SQL so beds=0 portals (Vivanuncios) remain candidates.
    assert "beds_min" not in params
    assert params["price_max_mxn"] == 12_000_000
