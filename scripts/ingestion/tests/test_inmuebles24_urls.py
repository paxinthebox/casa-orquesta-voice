"""Tests for Inmuebles24 search URL builder."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INGEST = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, INGEST)

from portals.inmuebles24_urls import inmuebles24_search_url  # noqa: E402


def test_cdmx_departamentos_venta():
    url = inmuebles24_search_url({
        "city": "Ciudad de México",
        "propertyType": "departamentos",
        "transactionType": "venta",
        "state": "CDMX",
    })
    assert url == (
        "https://www.inmuebles24.com/departamentos-en-venta-en-ciudad-de-mexico-df.html"
    )


def test_morelos_casas_renta():
    url = inmuebles24_search_url({
        "city": "Cuernavaca",
        "propertyType": "casas",
        "transactionType": "renta",
        "state": "Morelos",
    })
    assert url == "https://www.inmuebles24.com/casas-en-renta-en-cuernavaca-mor.html"


def test_cdmx_colonia_casas_venta():
    url = inmuebles24_search_url({
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "propertyType": "casas",
        "transactionType": "venta",
        "state": "CDMX",
    })
    assert url == (
        "https://www.inmuebles24.com/casas-en-venta-en-prados-churubusco-coyoacan-df.html"
    )


def test_morelos_fraccionamiento_venta():
    url = inmuebles24_search_url({
        "colonia": "Santa Fe Lifestyle",
        "city": "Xochitepec",
        "propertyType": "departamentos",
        "transactionType": "venta",
        "state": "Morelos",
    })
    assert url == (
        "https://www.inmuebles24.com/departamentos-en-venta-en-santa-fe-lifestyle-xochitepec-mor.html"
    )


def test_search_url_override():
    custom = "https://www.inmuebles24.com/inmuebles-en-venta-desde-6000000-pesos.html"
    assert inmuebles24_search_url({"searchUrl": custom}) == custom


def test_actor_profile_detection():
    from ingestion.portals.inmuebles24 import actor_profile, build_apify_run_input

    assert actor_profile("azzouzana/inmuebles24-scraper-pro-by-search-url") == "search_url"
    assert actor_profile("ecomscrape/inmuebles24-property-listings-scraper") == "ecomscrape"
    assert actor_profile("juandiaz.mx/inmuebles24-scraper") == "juandiaz"

    query = {
        "city": "Cuernavaca",
        "propertyType": "casas",
        "transactionType": "venta",
        "state": "Morelos",
    }
    payload = build_apify_run_input(query, profile="search_url", max_items=10, proxy=None)
    assert payload["startUrl"].endswith("cuernavaca-mor.html")
    assert payload["maxItems"] == 10
