"""Tests for shared listing filter helpers."""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, _SHARED)

from listing_filters import (  # noqa: E402
    apply_listing_mode_filter,
    apply_type_filter,
    apply_types_filter,
    infer_canonical_type,
    is_live_listing,
    listing_matches_type,
    listing_mode,
    normalize_type_filters,
)


def test_condo_title_matches_condominio_search():
    row = {"type": "casa", "title": "Casa en condominio en Cuernavaca", "listing_mode": "sale"}
    assert listing_matches_type(row, "condominio")


def test_rent_filter_excludes_short_term():
    rows = [
        {"id": "A", "listing_mode": "rent", "title": "Depto en renta anual"},
        {"id": "B", "listing_mode": "rent", "title": "Depto Airbnb por noche"},
        {"id": "C", "listing_mode": "sale", "title": "Casa en venta"},
    ]
    out = apply_listing_mode_filter(rows, "rent")
    assert [r["id"] for r in out] == ["A"]


def test_apartment_synonym():
    row = {"type": "inmueble", "title": "Apartamento en Polanco", "listing_mode": "sale"}
    matched = apply_type_filter([row], "departamento")
    assert matched == [row]


def test_apply_types_filter_accepts_any_selected_type():
    rows = [
        {"type": "departamento", "title": "Depto Roma"},
        {"type": "casa", "title": "Casa Coyoacán"},
        {"type": "terreno", "title": "Terreno"},
    ]
    matched = apply_types_filter(rows, ["departamento", "casa"])
    assert len(matched) == 2


def test_apply_types_filter_returns_empty_when_no_match():
    rows = [{"type": "terreno", "title": "Terreno Cuernavaca"}]
    assert apply_types_filter(rows, ["departamento", "casa"]) == []


def test_infer_canonical_type_prefers_title():
    assert infer_canonical_type(
        title="Casa en renta Cuernavaca",
        fallback="departamento",
    ) == "casa"


def test_title_beats_conflicting_description_for_type_match():
    row = {
        "type": "casa",
        "title": "Departamento en Rancho Cortes, Cuernavaca",
        "description": "Casa en Rancho Cortes, Cuernavaca Morelos",
        "listing_mode": "rent",
    }
    assert listing_matches_type(row, "departamento")
    assert not listing_matches_type(row, "casa")


def test_casa_title_rejected_for_departamento_despite_wrong_type_field():
    row = {
        "type": "departamento",
        "title": "Casa en Renta en Acacias, Cuernavaca",
        "listing_mode": "rent",
    }
    assert not listing_matches_type(row, "departamento")
    assert listing_matches_type(row, "casa")


def test_normalize_type_filters_single_type_drops_stale_types_array():
    out = normalize_type_filters({
        "type": "departamento",
        "types": ["departamento", "casa"],
        "property_types": ["departamento", "casa"],
    })
    assert out == {"type": "departamento"}


def test_listing_mode_inferred_from_rent_title_when_field_missing():
    row = {"title": "Departamento en Renta en Tres Cumbres Santa Fe", "price_mxn": 38000}
    assert listing_mode(row) == "rent"


def test_sale_search_excludes_inferred_rent_rows():
    rows = [
        {"title": "Departamento en Renta en Tres Cumbres Santa Fe", "price_mxn": 38000},
        {"title": "Casa en venta en Polanco", "listing_mode": "sale", "price_mxn": 8_000_000},
    ]
    out = apply_listing_mode_filter(rows, "sale")
    assert len(out) == 1
    assert "venta" in out[0]["title"].lower()


def test_listing_mode_title_overrides_wrong_sale_field():
    row = {
        "title": "Renta Casa en Las Lomas de Chapultepec",
        "listing_mode": "sale",
        "source_url": "https://www.inmuebles24.com/propiedades/clasificado/alclcain-renta-casa-143575030.html",
    }
    assert listing_mode(row) == "rent"


def test_listing_mode_prefers_venta_url_over_renta_o_venta_title():
    """Dual-title ads scraped from venta SERPs must stay sale (sale price)."""
    row = {
        "title": "Departamento en Renta o Venta en Av Juarez",
        "listing_mode": "rent",
        "source_url": (
            "https://www.vivanuncios.com.mx/a-venta-departamento/centro/"
            "departamento-en-renta-o-venta/5800780"
        ),
        "price_mxn": 5_800_780,
    }
    assert listing_mode(row) == "sale"


def test_listing_mode_reclassifies_cheap_sale_as_rent():
    """I24 often tags monthly Polanco rents as sale with a 60k price."""
    row = {
        "title": "Plaza Carso 2 Recamaras Amueblado",
        "listing_mode": "sale",
        "type": "departamento",
        "price_mxn": 60_000,
        "source_url": (
            "https://www.inmuebles24.com/propiedades/clasificado/"
            "alclapin-plaza-carso-2-recamaras-amueblado-65931558.html"
        ),
    }
    assert listing_mode(row) == "rent"


def test_mercadolibre_is_live_listing():
    assert is_live_listing({"id": "ML-123", "source": "mercadolibre"})
    assert is_live_listing({"id": "ML-123", "source": ""})
    assert not is_live_listing({"id": "L-CDMX-1", "source": "catalog_demo"})

