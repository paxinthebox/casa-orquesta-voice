"""Contact extraction from Inmuebles24 listings."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from ingestion.contact_extract import (  # noqa: E402
    agency_from_text,
    agent_from_text,
    enrich_listing_contacts,
    resolve_listing_contacts,
)
from ingestion.people_index import build_market_people_index  # noqa: E402


def test_agent_from_asesor_line():
    text = "Asesor inmobiliario: María Fernández López. Contáctanos hoy."
    assert "maría" in agent_from_text(text).lower()


def test_agent_rejects_description_fragment():
    text = "Asesor: con la naturaleza sin arriesgar tu privacidad"
    assert agent_from_text(text) == ""


def test_agency_from_remax_description():
    text = (
        "5796 Remax For Sale Inmobiliaria en Cuernavaca. "
        "¡re/max te ayuda a encontrar las mejores propiedades!"
    )
    assert "remax" in agency_from_text(text).lower()
    assert "inmobiliaria" in agency_from_text(text).lower()


def test_agency_from_rayo_vende():
    row = {
        "title": "Departamento en Venta en del valle",
        "description": (
            "¡Listo para estrenar departamento con Rayo Vende Inmobiliaria! "
            "¡Contáctanos hoy mismo!"
        ),
        "source": "inmuebles24",
    }
    publisher, _ = resolve_listing_contacts(row)
    assert "rayo vende" in publisher.lower()


def test_agency_from_inmobiliaria_mory():
    row = {
        "description": "Inmobiliaria Mory le presenta esta espectacular propiedad en Cuernavaca",
        "source": "inmuebles24",
    }
    publisher, _ = resolve_listing_contacts(row)
    assert "mory" in publisher.lower()


def test_enrich_cached_listings_produce_i24_brokers():
    cache = ROOT / "data" / "cache" / "market_listings.json"
    if not cache.is_file():
        return
    rows = json.loads(cache.read_text(encoding="utf-8"))
    i24 = [r for r in rows if r.get("source") == "inmuebles24"][:200]
    if not i24:
        return
    enriched = [enrich_listing_contacts(r) for r in i24]
    colabs, brokers = build_market_people_index(enriched)
    i24_brokers = [b for b in brokers if b.get("source") == "inmuebles24"]
    assert len(i24_brokers) >= 1, f"expected I24 brokers from cache, got {len(i24_brokers)}"
