"""Tests for Vivanuncios URL builder and portal validation."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portal_validation import (  # noqa: E402
    compare_colonia_queries,
    query_key,
    validate_query_batch,
)
from ingestion.portals.vivanuncios import normalize_vivanuncios_row  # noqa: E402
from ingestion.portals.vivanuncios_urls import vivanuncios_search_url  # noqa: E402


def test_vivanuncios_prados_churubusco_casas_venta_url():
    url = vivanuncios_search_url(
        {
            "colonia": "Prados Churubusco",
            "city": "Coyoacán",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "aliases": ["prados churubusco", "prado churubusco"],
        }
    )
    assert url == "https://www.vivanuncios.com.mx/s-casas-en-venta/prado-churubusco/"


def test_vivanuncios_colonia_override_url():
    url = vivanuncios_search_url(
        {
            "colonia": "Prados Churubusco",
            "propertyType": "casas",
            "transactionType": "venta",
            "vivanunciosUrl": (
                "https://www.vivanuncios.com.mx/s-casas-en-venta/"
                "prado-churubusco/v1c1293l13521p1"
            ),
        }
    )
    assert "prado-churubusco" in url
    assert "l13521" in url


def test_vivanuncios_roma_norte_uses_location_id_override():
    """Bare /roma-norte/ returns off-area SERP; location id l13669 is required."""
    url = vivanuncios_search_url(
        {
            "colonia": "Roma Norte",
            "city": "Cuauhtémoc",
            "state": "CDMX",
            "propertyType": "departamentos",
            "transactionType": "venta",
            "vivanunciosUrl": (
                "https://www.vivanuncios.com.mx/s-departamentos-en-venta/"
                "roma-norte/v1c1294l13669p1"
            ),
        }
    )
    assert url.endswith("roma-norte/v1c1294l13669p1")


def test_validate_query_batch_flags_colonia_zero_kept():
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prado churubusco"],
    }
    raw_rows = [
        {
            "posting_id": "999001",
            "url": "https://www.vivanuncios.com.mx/d-casa/999001",
            "title": "Casa en Puebla",
            "posting_location": {
                "location": {
                    "name": "Centro",
                    "parent": {"name": "Puebla", "parent": {"name": "Puebla"}},
                }
            },
        }
        for _ in range(6)
    ]

    def _reject(_raw, _query=None):
        return None

    report = validate_query_batch(
        portal="vivanuncios",
        query=query,
        raw_rows=raw_rows,
        kept_rows=[],
        search_url="https://example.test/prado-churubusco",
        flatten_fn=lambda row: row,
        normalize_fn=_reject,
    )
    assert report["status"] == "fail"
    assert report["raw_count"] == 6
    assert report["kept_count"] == 0


def test_validate_query_batch_passes_colonia_kept_rows():
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prado churubusco"],
    }
    kept = [
        {
            "title": "Casa en Prado Churubusco",
            "neighborhood": "Prado Churubusco",
            "city": "Coyoacán",
            "state": "CDMX",
            "price_mxn": 4900000,
        }
    ]
    report = validate_query_batch(
        portal="vivanuncios",
        query=query,
        raw_rows=kept,
        kept_rows=kept,
        search_url="https://example.test/prado-churubusco",
    )
    assert report["status"] == "pass"
    assert report["kept_colonia_hit_rate"] == 1.0


def test_compare_colonia_queries_across_portals():
    sources = {
        "inmuebles24": {
            "source": "inmuebles24",
            "queries": [
                {
                    "scope": "colonia",
                    "colonia": "Prados Churubusco",
                    "city": "Coyoacán",
                    "propertyType": "casas",
                    "transactionType": "venta",
                    "count": 0,
                    "raw_count": 80,
                    "validation": {"status": "fail", "query_key": query_key({})},
                }
            ],
        },
        "vivanuncios": {
            "source": "vivanuncios",
            "queries": [
                {
                    "scope": "colonia",
                    "colonia": "Prados Churubusco",
                    "city": "Coyoacán",
                    "propertyType": "casas",
                    "transactionType": "venta",
                    "count": 12,
                    "raw_count": 20,
                    "validation": {"status": "pass"},
                }
            ],
        },
    }
    rows = compare_colonia_queries(sources)
    assert len(rows) == 1
    assert rows[0]["portals"]["inmuebles24"]["kept_count"] == 0
    assert rows[0]["portals"]["vivanuncios"]["kept_count"] == 12


def test_normalize_vivanuncios_row_maps_navent_location():
    raw = {
        "posting_id": "141933259",
        "url": "https://www.vivanuncios.com.mx/d-casa/141933259",
        "title": "Casa en Prado Churubusco",
        "price_operation_types": [
            {"prices": [{"amount": 4900000, "currency": "MN"}]}
        ],
        "real_estate_type": {"name": "Casa"},
        "posting_location": {
            "location": {
                "name": "Prado Churubusco",
                "parent": {
                    "name": "Coyoacán",
                    "parent": {"name": "Ciudad de México", "acronym": "CDMX"},
                },
            }
        },
    }
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prado churubusco"],
    }
    row = normalize_vivanuncios_row(raw, query=query)
    assert row is not None
    assert row["id"] == "VA-141933259"
    assert row["source"] == "vivanuncios"
    assert row["neighborhood"] == "Prado Churubusco"
    assert row["price_mxn"] == 4900000
