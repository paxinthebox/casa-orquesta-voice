"""Tests for cross-portal listing dedupe."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.dedupe import dedupe_listings, listing_fingerprint  # noqa: E402


def _base_row(**overrides):
    row = {
        "state": "CDMX",
        "city": "Coyoacán",
        "neighborhood": "Prado Churubusco",
        "price_mxn": 4_990_000,
        "beds": 4,
        "m2": 345,
        "title": "Casa en Prado Churubusco",
        "description": "Casa amplia",
    }
    row.update(overrides)
    return row


def test_same_fingerprint_keeps_single_row():
    va = _base_row(id="VA-1", source="vivanuncios", source_url="https://vivanuncios.test/1")
    prop = _base_row(
        id="PROP-2",
        source="propiedades",
        source_url="https://propiedades.com/2",
        description="Casa amplia con jardín",
    )
    assert listing_fingerprint(va) == listing_fingerprint(prop)
    out = dedupe_listings([va, prop])
    assert len(out) == 1
    assert out[0]["id"] == "PROP-2"


def test_dedupe_records_alternate_sources():
    va = _base_row(id="VA-1", source="vivanuncios", source_url="https://vivanuncios.test/1")
    prop = _base_row(
        id="PROP-2",
        source="propiedades",
        source_url="https://propiedades.com/2",
    )
    out = dedupe_listings([va, prop])
    alts = out[0].get("alternate_sources") or []
    assert len(alts) == 1
    assert alts[0]["id"] == "PROP-2"
    assert alts[0]["source"] == "propiedades"
