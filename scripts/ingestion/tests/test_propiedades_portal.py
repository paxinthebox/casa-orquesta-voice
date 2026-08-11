"""Tests for Propiedades.com portal adapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.propiedades import (  # noqa: E402
    normalize_propiedades_row,
    propiedades_listing_id_from_url,
)
from ingestion.portals.propiedades_queries import (  # noqa: E402
    propiedades_deal_type,
    propiedades_location,
    propiedades_market_queries,
    propiedades_property_type,
)


def test_propiedades_location_prados_alias():
    query = {
        "colonia": "Prados Churubusco",
        "aliases": ["prado churubusco", "prados churubusco"],
    }
    assert propiedades_location(query) == "Prado Churubusco"


def test_propiedades_apify_input_mapping():
    query = {
        "colonia": "Prados Churubusco",
        "propertyType": "departamentos",
        "transactionType": "renta",
        "scope": "colonia",
    }
    assert propiedades_property_type(query) == "apartment"
    assert propiedades_deal_type(query) == "rent"


def test_propiedades_location_coyoacan_borough():
    query = {"city": "Coyoacán", "state": "CDMX", "scope": "borough", "propertyType": "casas"}
    assert propiedades_location(query) == "Coyoacán"


def test_propiedades_location_cuernavaca_city():
    query = {"city": "Cuernavaca", "state": "Morelos", "scope": "city", "propertyType": "casas"}
    assert propiedades_location(query) == "Cuernavaca"


def test_propiedades_market_queries_includes_borough_and_colonia():
    queries = [
        {"city": "Coyoacán", "state": "CDMX", "scope": "borough", "propertyType": "casas"},
        {"colonia": "Roma Norte", "state": "CDMX", "scope": "colonia", "propertyType": "casas"},
        {"city": "Querétaro", "state": "Querétaro", "propertyType": "casas"},
    ]
    out = propiedades_market_queries(queries)
    assert len(out) == 2
    assert out[0]["city"] == "Coyoacán"
    assert out[1]["colonia"] == "Roma Norte"


def test_propiedades_listing_id_from_url():
    url = (
        "https://propiedades.com/inmuebles/casa-en-venta-casiopea-70-"
        "prado-churubusco-df-30251056"
    )
    assert propiedades_listing_id_from_url(url) == "30251056"


def test_normalize_propiedades_row_from_pilot_payload():
    pilot_path = SCRIPTS.parent / "data" / "cache" / "propiedades_prados_pilot.json"
    raw = json.loads(pilot_path.read_text(encoding="utf-8"))
    casiopea = next(row for row in raw if row.get("record_id") == "30251056")
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prado churubusco"],
    }
    row = normalize_propiedades_row(casiopea, query)
    assert row is not None
    assert row["id"] == "PROP-30251056"
    assert row["source"] == "propiedades"
    assert row["price_mxn"] == 4_990_000
    assert row["neighborhood"] == "Prado Churubusco"
    assert row["listing_mode"] == "sale"
    assert "propiedades.com" in row["source_url"]
