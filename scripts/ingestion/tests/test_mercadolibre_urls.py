"""Tests for Mercado Libre Inmuebles URL builder."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.mercadolibre_urls import mercadolibre_search_url  # noqa: E402


def test_coyoacan_borough_casas_venta_url():
    url = mercadolibre_search_url(
        {
            "city": "Coyoacán",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "scope": "borough",
        }
    )
    assert url == (
        "https://inmuebles.mercadolibre.com.mx/casas/venta/distrito-federal/coyoacan/"
    )


def test_prados_churubusco_colonia_url():
    url = mercadolibre_search_url(
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
        "https://inmuebles.mercadolibre.com.mx/casas/venta/"
        "distrito-federal/coyoacan/prado-churubusco/"
    )


def test_cdmx_wide_departamentos_renta_url():
    url = mercadolibre_search_url(
        {
            "city": "Ciudad de México",
            "state": "CDMX",
            "propertyType": "departamentos",
            "transactionType": "renta",
        }
    )
    assert url == (
        "https://inmuebles.mercadolibre.com.mx/departamentos/renta/distrito-federal/"
    )


def test_cuernavaca_morelos_casas_venta_url():
    url = mercadolibre_search_url(
        {
            "city": "Cuernavaca",
            "state": "Morelos",
            "propertyType": "casas",
            "transactionType": "venta",
            "scope": "borough",
        }
    )
    assert url == (
        "https://inmuebles.mercadolibre.com.mx/casas/venta/morelos/cuernavaca/"
    )


def test_mercadolibre_url_override():
    override = (
        "https://inmuebles.mercadolibre.com.mx/departamentos/renta/"
        "distrito-federal/coyoacan/?foo=1"
    )
    url = mercadolibre_search_url(
        {
            "city": "Coyoacán",
            "state": "CDMX",
            "propertyType": "casas",
            "transactionType": "venta",
            "mercadolibreUrl": override,
        }
    )
    assert url == override.split("?")[0]
