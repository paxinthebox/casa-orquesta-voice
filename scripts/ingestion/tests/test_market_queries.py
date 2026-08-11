"""Tests for market query expansion and geo validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.market_geo import (  # noqa: E402
    CDMX_MARKET_BOROUGHS,
    MORELOS_MARKET_CITIES,
    expand_colonia_queries,
    expand_market_queries,
    matches_market_query,
)
from ingestion.portals.inmuebles24 import load_market_queries  # noqa: E402


def test_morelos_state_scope_expands_to_all_cities():
    raw = [
        {"scope": "state", "propertyType": "casas", "transactionType": "venta", "state": "Morelos"},
    ]
    expanded = expand_market_queries(raw)
    assert len(expanded) == len(MORELOS_MARKET_CITIES)
    assert {q["city"] for q in expanded} == set(MORELOS_MARKET_CITIES)


def test_cdmx_borough_scope_expands():
    raw = [
        {"scope": "boroughs", "propertyType": "departamentos", "transactionType": "renta", "state": "CDMX"},
    ]
    expanded = expand_market_queries(raw)
    assert len(expanded) == len(CDMX_MARKET_BOROUGHS)
    assert {q["city"] for q in expanded} == set(CDMX_MARKET_BOROUGHS)


def test_state_wide_query_not_expanded():
    raw = [
        {
            "scope": "state_wide",
            "searchUrl": "https://www.inmuebles24.com/casas-en-renta-en-morelos-mor.html",
            "propertyType": "casas",
            "transactionType": "renta",
            "state": "Morelos",
        },
    ]
    expanded = expand_market_queries(raw)
    assert len(expanded) == 1
    assert expanded[0]["scope"] == "state_wide"


def test_rent_queries_file_expansion():
    path = SCRIPTS.parent / "data" / "market_queries_rent.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    expanded = expand_market_queries(raw)
    expected = (
        2 * len(CDMX_MARKET_BOROUGHS)  # depto + casa × boroughs
        + 2  # state_wide Morelos
        + 2 * len(MORELOS_MARKET_CITIES)  # per-municipality
    )
    assert len(expanded) == expected


def test_market_queries_file_expands_pilot_regions():
    path = SCRIPTS.parent / "data" / "market_queries.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    expanded = expand_market_queries(raw)
    mor_cities = {q["city"] for q in expanded if q.get("state") == "Morelos" and q.get("city")}
    assert mor_cities == set(MORELOS_MARKET_CITIES)
    cdmx_boroughs = {
        q["city"] for q in expanded
        if q.get("state") == "CDMX" and q.get("scope") == "borough"
    }
    assert cdmx_boroughs == set(CDMX_MARKET_BOROUGHS)
    venta_boroughs = {
        q["city"] for q in expanded
        if q.get("state") == "CDMX"
        and q.get("scope") == "borough"
        and q.get("transactionType") == "venta"
    }
    assert venta_boroughs == set(CDMX_MARKET_BOROUGHS)


def test_colonia_queries_expand_and_match():
    raw = [
        {
            "colonia": "Prados Churubusco",
            "city": "Coyoacán",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "aliases": ["prados churubusco"],
        },
    ]
    expanded = expand_colonia_queries(raw)
    assert expanded[0]["scope"] == "colonia"
    query = expanded[0]
    assert matches_market_query(
        {
            "state": "CDMX",
            "city": "Coyoacán",
            "neighborhood": "Prados Churubusco",
            "title": "Casa en Prados Churubusco",
        },
        query,
    )
    assert not matches_market_query(
        {
            "state": "CDMX",
            "city": "Benito Juárez",
            "neighborhood": "Portales Norte",
            "description": "cerca de Av. Rio Churubusco",
        },
        query,
    )


def test_colonia_accepts_cdmx_generic_city_when_colonia_matches():
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prados churubusco", "prado churubusco"],
    }
    assert matches_market_query(
        {
            "state": "CDMX",
            "city": "Ciudad de México",
            "neighborhood": "Prado Churubusco",
            "title": "Casa en venta en Prado Churubusco, Coyoacán",
        },
        query,
    )


def test_load_market_queries_includes_colonias_file():
    expanded = load_market_queries()
    colonias = [q for q in expanded if q.get("scope") == "colonia"]
    assert any(q.get("colonia") == "Prados Churubusco" for q in colonias)
    assert any(q.get("colonia") == "Santa Fe Lifestyle" for q in colonias)


def test_matches_market_query_accepts_same_state_different_city():
    """Portal list pages mix municipalities — ingest keeps in-state rows."""
    item = {"state": "Morelos", "city": "Temixco", "title": "Casa en Temixco"}
    query = {"state": "Morelos", "city": "Jiutepec", "scope": "city"}
    assert matches_market_query(item, query)


def test_matches_market_query_state_wide_morelos():
    item = {"state": "Morelos", "city": "Xochitepec", "title": "Casa en Xochitepec"}
    query = {"state": "Morelos", "scope": "state_wide"}
    assert matches_market_query(item, query)


def test_load_market_queries_integration(monkeypatch):
    monkeypatch.delenv("MARKET_QUERIES_PATH", raising=False)
    expanded = load_market_queries()
    assert any(q.get("scope") == "state_wide" for q in expanded)
    assert any(q.get("scope") == "borough" for q in expanded)
