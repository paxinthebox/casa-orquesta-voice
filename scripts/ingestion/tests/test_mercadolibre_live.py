"""Tests for Mercado Libre live SERP parse + live Apify input."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.mercadolibre import (  # noqa: E402
    build_apify_run_input,
    build_live_apify_run_input,
    mercadolibre_live_enabled,
    normalize_mercadolibre_row,
)
from ingestion.portals.mercadolibre_live_parse import (  # noqa: E402
    page_url,
    parse_serp_html,
)

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "fixtures"
    / "mercadolibre_coyoacan_serp.html"
)


def test_parse_serp_html_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    start = "https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/"
    rows = parse_serp_html(html, start_url=start)
    assert len(rows) == 3
    assert rows[0]["item_id"] == "2276252287"
    assert rows[0]["price"] == 18500


def test_page_url_desde_pagination():
    start = "https://inmuebles.mercadolibre.com.mx/departamentos/venta/distrito-federal/coyoacan/"
    assert page_url(start, 2).endswith("/coyoacan/_Desde_49/")


def test_build_live_apify_run_input(monkeypatch):
    monkeypatch.setenv("INGEST_MERCADOLIBRE_LIVE", "1")
    query = {
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "departamentos",
        "transactionType": "venta",
    }
    run_input = build_live_apify_run_input(
        query,
        max_items=25,
        proxy={"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    )
    assert run_input["maxItems"] == 25
    assert run_input["proxyConfiguration"]["apifyProxyCountry"] == "MX"
    assert "coyoacan" in run_input["startUrl"]


def test_normalize_live_row(monkeypatch):
    monkeypatch.setenv("INGEST_MERCADOLIBRE_LIVE", "1")
    raw = {
        "item_id": "2276252287",
        "url": "https://departamento.mercadolibre.com.mx/MLM-2276252287-test-_JM",
        "title": "Departamento en renta Coyoacán",
        "price": 18500,
        "currency": "MXN",
        "location": "Cantil Del Pedregal, Coyoacán, Distrito Federal",
        "neighborhood": "Cantil Del Pedregal",
        "city": "Coyoacán",
        "state": "CDMX",
        "bedrooms": 2,
        "bathrooms": 1,
        "area_m2": 65,
        "property_type": "departamento",
        "listing_mode": "rent",
    }
    query = {
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "departamentos",
        "transactionType": "renta",
    }
    row = normalize_mercadolibre_row(raw, query=query)
    assert row is not None
    assert row["id"] == "ML-2276252287"
    assert row["price_mxn"] == 18500
    assert row["state"] == "CDMX"
    assert row["listing_mode"] == "rent"


def test_mercadolibre_live_disabled_by_default(monkeypatch):
    monkeypatch.delenv("INGEST_MERCADOLIBRE_LIVE", raising=False)
    assert mercadolibre_live_enabled() is False
    query = {"city": "Coyoacán", "state": "CDMX", "propertyType": "departamentos", "transactionType": "venta"}
    legacy = build_apify_run_input(query, max_items=10, proxy=None)
    assert "urls" in legacy
    assert "startUrl" not in legacy
