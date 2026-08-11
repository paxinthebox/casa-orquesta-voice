"""Tests for financing extraction and mortgage search filter."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from financing import (  # noqa: E402
    apply_financing_filter,
    enrich_listing_financing,
    extract_accepts_financing,
    listing_accepts_mortgage,
)


def test_extract_infonavit_fovissste_bancario():
    text = (
        "Se acepta crédito Infonavit, Fovissste ó bancarios. "
        "Nosotros hacemos todos los trámites."
    )
    tags = extract_accepts_financing(text)
    assert "INFONAVIT" in tags
    assert "FOVISSSTE" in tags
    assert "bancario" in tags


def test_extract_cofinavit_maps_to_both():
    tags = extract_accepts_financing("Crédito cofinavit disponible")
    assert "INFONAVIT" in tags
    assert "cofinanciamiento" in tags


def test_extract_contado_only():
    tags = extract_accepts_financing("Precios especiales para inversionistas en pago de contado")
    assert tags == ["contado"]


def test_listing_accepts_infonavit_query():
    row = enrich_listing_financing({
        "id": "I24-1",
        "description": "Aceptamos crédito Infonavit y bancario",
    })
    assert listing_accepts_mortgage(row, "INFONAVIT")
    assert listing_accepts_mortgage(row, "bancario")
    assert not listing_accepts_mortgage(row, "FOVISSSTE")


def test_apply_financing_filter_strict():
    rows = [
        enrich_listing_financing({"id": "a", "description": "acepta infonavit"}),
        enrich_listing_financing({"id": "b", "description": "solo contado"}),
        {"id": "c", "description": "sin mención de crédito"},
    ]
    out = apply_financing_filter(rows, "INFONAVIT")
    assert [r["id"] for r in out] == ["a"]


def test_on_the_fly_description_fallback():
    row = {"id": "x", "description": "Crédito FOVISSSTE bienvenido"}
    assert listing_accepts_mortgage(row, "FOVISSSTE")


if __name__ == "__main__":
    test_extract_infonavit_fovissste_bancario()
    test_extract_cofinavit_maps_to_both()
    test_extract_contado_only()
    test_listing_accepts_infonavit_query()
    test_apply_financing_filter_strict()
    test_on_the_fly_description_fallback()
    print("test_financing: all passed")
