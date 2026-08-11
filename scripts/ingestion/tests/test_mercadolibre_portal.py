"""Tests for Mercado Libre portal normalize + Apify input."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.mercadolibre import (  # noqa: E402
    build_apify_run_input,
    mercadolibre_item_id_from_url,
    normalize_mercadolibre_row,
)


def test_mercadolibre_item_id_from_permalink():
    url = (
        "https://departamento.mercadolibre.com.mx/MLM-2276252287-"
        "habitacion-en-renta-pb-en-cantil-coyoacan-_JM"
    )
    assert mercadolibre_item_id_from_url(url) == "2276252287"


def test_build_apify_run_input_uses_inmuebles_url_and_mx_proxy():
    query = {
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "departamentos",
        "transactionType": "renta",
        "scope": "borough",
    }
    run_input = build_apify_run_input(
        query,
        max_items=25,
        proxy={"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    )
    assert run_input["max_items_per_url"] == 25
    assert run_input["proxy"]["apifyProxyCountry"] == "MX"
    assert run_input["urls"][0].endswith("/distrito-federal/coyoacan/")


def test_normalize_ecomscrape_row_maps_components():
    raw = {
        "id": "MLM3581877590",
        "components": [
            {
                "type": "title",
                "title": {"text": "Departamento Renta Av. Del Iman Coyoacan"},
            },
            {
                "type": "price",
                "price": {
                    "current_price": {"value": 18500, "currency": "MXN"},
                },
            },
            {
                "type": "location",
                "location": {"text": "Cantil Del Pedregal, Coyoacán, Distrito Federal"},
            },
        ],
    }
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "departamentos",
        "transactionType": "renta",
        "scope": "colonia",
    }
    row = normalize_mercadolibre_row(raw, query=query)
    assert row is not None
    assert row["id"] == "ML-3581877590"
    assert row["source"] == "mercadolibre"
    assert row["price_mxn"] == 18500
    assert row["listing_mode"] == "rent"


def test_normalize_flat_actor_row():
    raw = {
        "title": "Casa en venta Cuernavaca",
        "price": 3200000,
        "currency": "MXN",
        "url": "https://casa.mercadolibre.com.mx/MLM-1234567890-casa-cuernavaca-_JM",
    }
    query = {
        "city": "Cuernavaca",
        "state": "Morelos",
        "propertyType": "casas",
        "transactionType": "venta",
    }
    row = normalize_mercadolibre_row(raw, query=query)
    assert row is not None
    assert row["id"] == "ML-1234567890"
    assert row["price_mxn"] == 3200000
