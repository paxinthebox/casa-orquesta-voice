"""Tests for Lamudi inventory adapter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.lamudi import (  # noqa: E402
    fetch_lamudi_market,
    lamudi_live_enabled,
    normalize_lamudi_row,
)
from ingestion.portals.lamudi_geo import parse_lamudi_title_location  # noqa: E402
from ingestion.portals.lamudi_queries import (  # noqa: E402
    lamudi_title_matches_query,
)


def test_parse_prado_churubusco_title():
    title = (
        "Casa en Venta en Prado Churubusco Prado Churubusco, Coyoacán, "
        "Ciudad de México"
    )
    nbhd, city, state = parse_lamudi_title_location(title)
    assert nbhd == "Prado Churubusco"
    assert city == "Coyoacán"
    assert state == "CDMX"


def test_reject_puebla_for_cdmx_colonia_query():
    title = (
        "Casa en Fraccionamiento en Venta en Lomas de Angelópolis "
        "Lomas de Angelópolis, Puebla, Puebla Casa en Venta Nueva en Parque Cuernavaca"
    )
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "aliases": ["prado churubusco"],
    }
    assert lamudi_title_matches_query(title, query) is False


def test_normalize_prado_row_from_pilot():
    pilot_path = SCRIPTS.parent / "data" / "cache" / "lamudi_pilot.json"
    rows = json.loads(pilot_path.read_text(encoding="utf-8"))
    prado = next(
        r for r in rows
        if "Prado Churubusco" in str(r.get("title") or "")
    )
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "scope": "colonia",
        "aliases": ["prado churubusco"],
    }
    row = normalize_lamudi_row(prado, query)
    assert row is not None
    assert row["id"].startswith("LAM-")
    assert row["source"] == "lamudi"
    assert row["neighborhood"] == "Prado Churubusco"
    assert row["state"] == "CDMX"


def test_live_mode_skips_without_actor(monkeypatch):
    monkeypatch.setenv("INGEST_LAMUDI_LIVE", "1")
    monkeypatch.delenv("APIFY_LAMUDI_ACTOR", raising=False)

    import ingestion.portals.lamudi as lamudi_mod

    monkeypatch.setattr(lamudi_mod, "APIFY_LIVE_ACTOR", "")

    async def _run():
        listings, meta = await fetch_lamudi_market(queries=[{"city": "Cuernavaca"}])
        assert listings == []
        assert meta.get("skipped") is True
        assert "APIFY_LAMUDI_ACTOR" in str(meta.get("reason"))

    import asyncio

    asyncio.run(_run())


def test_normalize_live_row_with_portal_geo(monkeypatch):
    monkeypatch.setenv("INGEST_LAMUDI_LIVE", "1")
    import ingestion.portals.lamudi as lamudi_mod

    monkeypatch.setattr(lamudi_mod, "APIFY_LIVE_ACTOR", "org/lamudi-mx-scraper")

    raw = {
        "url": "https://www.lamudi.com.mx/detalle/abc123",
        "title": "Casa en venta",
        "price": 3500000,
        "bedrooms": 3,
        "bathrooms": 2,
        "area_m2": 120,
        "propertyType": "house",
        "neighborhood": "Prado Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
    }
    query = {
        "colonia": "Prados Churubusco",
        "city": "Coyoacán",
        "state": "CDMX",
        "propertyType": "casas",
        "transactionType": "venta",
        "aliases": ["prado churubusco"],
    }
    row = normalize_lamudi_row(raw, query)
    assert row is not None
    assert row["id"] == "LAM-abc123"
    assert row["neighborhood"] == "Prado Churubusco"


def test_lamudi_live_disabled_by_default():
    assert lamudi_live_enabled() is False
