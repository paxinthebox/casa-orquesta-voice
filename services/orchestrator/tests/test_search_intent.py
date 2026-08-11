"""Search intent + type canonicalization contract tests."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(ORCH, "..", "_shared"))
MATCHING = os.path.normpath(os.path.join(ORCH, "..", "matching"))
sys.path.insert(0, ORCH)
sys.path.insert(0, SHARED)
sys.path.insert(0, MATCHING)

from listing_filters import (  # noqa: E402
    apply_type_filter,
    canonicalize_listing_row,
    infer_canonical_type,
    listing_matches_type,
)
from search_intent import resolve_search_filters  # noqa: E402


def test_infer_canonical_type_casa_title_beats_departamento_field():
    assert infer_canonical_type(
        title="Casa en Renta en Acacias, Cuernavaca",
        description="",
        fallback="departamento",
    ) == "casa"


def test_infer_canonical_type_departamento_from_title():
    assert infer_canonical_type(
        title="Departamento en Rancho Cortes, Cuernavaca",
        fallback="inmueble",
    ) == "departamento"


def test_infer_canonical_type_title_beats_conflicting_description():
    assert infer_canonical_type(
        title="Departamento en Rancho Cortes, Cuernavaca",
        description="Casa en Rancho Cortes, Cuernavaca Morelos",
        fallback="casa",
    ) == "departamento"


def test_infer_canonical_type_url_overrides_misleading_casa_title():
    assert infer_canonical_type(
        title="Casa en Club de Golf Santa Fe, Xochitepec",
        description="Departamento en venta en Santa Fe Lifestyle, Xochitepec – Estrena tu nuevo hogar.",
        fallback="casa",
        source_url="https://www.inmuebles24.com/propiedades/clasificado/veclapin-departamento-en-venta-con-2-recamaras-147492248.html",
    ) == "departamento"


def test_canonicalize_listing_row_updates_type():
    row = canonicalize_listing_row({
        "id": "I24-1",
        "type": "departamento",
        "title": "Casa en Renta en Vista Hermosa",
    })
    assert row["type"] == "casa"


def test_canonicalize_aligns_departamento_title():
    row = canonicalize_listing_row({
        "id": "I24-147492248",
        "type": "casa",
        "title": "Casa en Club de Golf Santa Fe, Xochitepec",
        "description": "Departamento en venta en Santa Fe Lifestyle, Xochitepec",
        "source_url": "https://www.inmuebles24.com/propiedades/clasificado/veclapin-departamento-en-venta-147492248.html",
        "source": "inmuebles24",
        "listing_mode": "sale",
    })
    assert row["type"] == "departamento"
    assert row["title"].startswith("Departamento")
    assert not row["title"].lower().startswith("casa ")
    assert row.get("neighborhood") == "Santa Fe Lifestyle"
    assert "Santa Fe Lifestyle" in row["title"]
    assert "Club de Golf" not in row["title"]


def test_align_listing_headline_rewrites_casa_prefix():
    from listing_filters import align_listing_headline

    assert align_listing_headline(
        "Casa en Lázaro Cárdenas, Xochitepec",
        "departamento",
    ) == "Departamento en Lázaro Cárdenas, Xochitepec"


def test_departamento_query_excludes_casa_rows():
    rows = [
        {"type": "departamento", "title": "Casa en Renta", "listing_mode": "rent"},
        {"type": "departamento", "title": "Departamento en Centro", "listing_mode": "rent"},
    ]
    rows = [canonicalize_listing_row(r) for r in rows]
    out = apply_type_filter(rows, "departamento")
    assert [r["title"] for r in out] == ["Departamento en Centro"]


def test_resolve_departamento_clears_stale_dual_types():
    resolved = resolve_search_filters(
        {
            "client_profile": {},
            "filters": {"types": ["departamento", "casa"], "state": "Morelos"},
        },
        "Busco departamento en renta en Cuernavaca hasta 16 mil",
    )
    assert resolved.get("type") == "departamento"
    assert "types" not in resolved


def test_resolve_dual_type_only_when_both_named():
    resolved = resolve_search_filters(
        {"client_profile": {}, "filters": {}},
        "casa o departamento en renta en Cuernavaca",
    )
    assert resolved.get("types") == ["departamento", "casa"]


def test_listing_matches_type_after_canonicalize():
    row = canonicalize_listing_row({
        "type": "departamento",
        "title": "Casa Sola en Renta",
    })
    assert not listing_matches_type(row, "departamento")
    assert listing_matches_type(row, "casa")
