"""Tests for Lamudi URL builder."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.lamudi_urls import lamudi_search_url  # noqa: E402


def test_prado_churubusco_casas_venta_url():
    url = lamudi_search_url(
        {
            "colonia": "Prados Churubusco",
            "city": "Coyoacán",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "aliases": ["prado churubusco"],
        }
    )
    assert url == (
        "https://www.lamudi.com.mx/distrito-federal/coyoacan/"
        "prado-churubusco/for-sale/"
    )


def test_cuernavaca_departamentos_override():
    url = lamudi_search_url(
        {
            "city": "Cuernavaca",
            "state": "Morelos",
            "propertyType": "departamentos",
            "transactionType": "venta",
            "lamudiUrl": (
                "https://www.lamudi.com.mx/morelos/cuernavaca/"
                "departamento/departamentos/for-sale/"
            ),
        }
    )
    assert "departamento/departamentos/for-sale" in url


def test_cuernavaca_casas_morelos_path():
    url = lamudi_search_url(
        {
            "city": "Cuernavaca",
            "state": "Morelos",
            "propertyType": "casas",
            "transactionType": "venta",
        }
    )
    assert url == (
        "https://www.lamudi.com.mx/morelos/cuernavaca/for-sale/"
    )


def test_roma_norte_borough_slug():
    url = lamudi_search_url(
        {
            "colonia": "Roma Norte",
            "city": "Cuauhtémoc",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "lamudiCitySlug": "cuauhtemoc",
        }
    )
    assert url == (
        "https://www.lamudi.com.mx/distrito-federal/cuauhtemoc/"
        "roma-norte/for-sale/"
    )
