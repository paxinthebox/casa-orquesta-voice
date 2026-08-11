"""Near-match fallback when strict filters return zero listings."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(SVC, "..", "_shared"))
sys.path.insert(0, SVC)
sys.path.insert(0, SHARED)

from near_match import (  # noqa: E402
    apply_strict_filters,
    find_near_matches,
    near_match_price_penalty,
)


def _row(**kwargs) -> dict:
    base = {
        "id": "I24-1",
        "source": "inmuebles24",
        "listing_mode": "rent",
        "state": "Morelos",
        "city": "Cuernavaca",
        "neighborhood": "Vista Hermosa",
        "price_mxn": 20_000,
        "beds": 3,
        "type": "casa",
    }
    base.update(kwargs)
    return base


def test_strict_filters_drop_over_budget():
    rows = [_row(price_mxn=20_000), _row(id="I24-2", price_mxn=15_000)]
    out = apply_strict_filters(
        rows,
        filters={"listing_mode": "rent", "state": "Morelos", "price_max_mxn": 16_000},
        query="renta Cuernavaca 16000",
        pilot_states=None,
    )
    assert [r["id"] for r in out] == ["I24-2"]


def test_near_match_relaxes_price_when_strict_empty():
    rows = [_row(price_mxn=20_000), _row(id="I24-2", price_mxn=21_000)]
    out, relax = find_near_matches(
        rows,
        filters={"listing_mode": "rent", "state": "Morelos", "price_max_mxn": 16_000},
        query="renta Cuernavaca norte 16000",
        pilot_states=None,
    )
    assert "price_max_mxn" in relax
    assert {r["id"] for r in out} == {"I24-1", "I24-2"}


def test_near_match_does_not_pad_over_budget_when_under_budget_exists():
    rows = [
        _row(id="ok", price_mxn=15_000),
        _row(id="over", price_mxn=22_000),
    ]
    out, relax = find_near_matches(
        rows,
        filters={"listing_mode": "rent", "state": "Morelos", "price_max_mxn": 16_000},
        query="renta Cuernavaca 16000",
        pilot_states=None,
        limit=8,
    )
    assert "price_max_mxn" not in relax
    assert [r["id"] for r in out] == ["ok"]


def test_near_match_does_not_return_unbounded_over_budget():
    """When nothing is in-budget, stay within 1.5× — never rank the whole market."""
    rows = [
        _row(id="near", price_mxn=20_000),
        _row(id="far", price_mxn=80_000),
    ]
    out, relax = find_near_matches(
        rows,
        filters={"listing_mode": "rent", "state": "Morelos", "price_max_mxn": 16_000},
        query="renta Cuernavaca 16000",
        pilot_states=None,
        limit=8,
    )
    assert "price_max_mxn" in relax
    assert [r["id"] for r in out] == ["near"]


def test_strict_filters_use_title_beds_when_field_missing():
    loft = _row(
        id="VA-loft",
        beds=0,
        price_mxn=5_000_000,
        listing_mode="sale",
        type="departamento",
        title="Departamentos Tipo Loft de 1 Recámara Ideales Para Negocio",
        neighborhood="Condesa",
        city="Cuauhtémoc",
        state="CDMX",
    )
    ok = _row(
        id="VA-ok",
        beds=0,
        price_mxn=6_000_000,
        listing_mode="sale",
        type="departamento",
        title="Departamento 2 Recámaras en Condesa",
        neighborhood="Condesa",
        city="Cuauhtémoc",
        state="CDMX",
    )
    out = apply_strict_filters(
        [loft, ok],
        filters={
            "listing_mode": "sale",
            "state": "CDMX",
            "colonia": "Condesa",
            "neighborhood": "Condesa",
            "price_max_mxn": 8_000_000,
            "beds_min": 2,
            "type": "departamento",
        },
        query="departamento Condesa",
        pilot_states=["CDMX"],
    )
    assert [r["id"] for r in out] == ["VA-ok"]


def test_near_match_relaxes_location_within_state():
    rows = [
        _row(neighborhood="Centro", city="Cuautla"),
        _row(id="I24-2", neighborhood="Centro", city="Jiutepec"),
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "rent",
            "state": "Morelos",
            "neighborhood": "Lomas de Cortés",
            "city": "Cuernavaca",
        },
        query="lomas de cortés renta",
        pilot_states=None,
    )
    assert "location" in relax
    assert len(out) == 2


def test_near_match_zone_widens_to_municipio_for_portal_mix():
    rows = [
        _row(
            id="LAM-1",
            source="lamudi",
            city="Cuernavaca",
            neighborhood="Lomas de Cortés",
            listing_mode="sale",
            type="casa",
            price_mxn=2_000_000,
        ),
        _row(
            id="ML-1",
            source="mercadolibre",
            city="Cuernavaca",
            neighborhood="Centro",
            listing_mode="sale",
            type="casa",
            price_mxn=2_100_000,
        ),
        _row(
            id="VA-1",
            source="vivanuncios",
            city="Cuernavaca",
            neighborhood="Vista Hermosa",
            listing_mode="sale",
            type="casa",
            price_mxn=1_900_000,
        ),
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "sale",
            "state": "Morelos",
            "city": "Cuernavaca",
            "zone": "norte de cuernavaca",
            "type": "casa",
            "price_max_mxn": 2_300_000,
        },
        query="casa en venta al norte de cuernavaca",
        pilot_states=None,
        limit=8,
    )
    assert "zone" in relax
    sources = {r.get("source") for r in out}
    assert "lamudi" in sources
    assert len(sources) >= 2


def test_near_match_relaxes_type_in_same_city_before_location():
    rows = [
        _row(id="I24-xoch", city="Xochitepec", neighborhood="Los Laureles", type="casa"),
        _row(id="I24-cuer", city="Cuernavaca", neighborhood="Centro", type="departamento"),
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "rent",
            "state": "Morelos",
            "city": "Xochitepec",
            "type": "departamento",
        },
        query="departamento en renta Xochitepec",
        pilot_states=None,
    )
    assert "type" in relax
    assert "location" not in relax
    assert [r["id"] for r in out] == ["I24-xoch"]


def test_near_match_does_not_widen_municipio_to_other_cities():
    rows = [
        _row(id="I24-xoch", city="Xochitepec", type="casa"),
        _row(id="I24-cuer", city="Cuernavaca", type="departamento"),
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "sale",
            "state": "Morelos",
            "city": "Xochitepec",
            "type": "departamento",
        },
        query="departamentos en venta en Xochitepec Morelos",
        pilot_states=None,
    )
    assert out == []
    assert "location" not in relax


def test_near_match_price_penalty_prefers_closer_budget():
    assert near_match_price_penalty(_row(price_mxn=17_000), 16_000) > near_match_price_penalty(
        _row(price_mxn=21_000), 16_000,
    )


def test_near_match_no_type_widen_when_casa_explicit_in_colonia():
    rows = [
      {
          "id": "I24-dep",
          "source": "inmuebles24",
          "listing_mode": "sale",
          "state": "CDMX",
          "city": "Coyoacán",
          "neighborhood": "Prados Churubusco",
          "price_mxn": 4_000_000,
          "type": "departamento",
          "title": "Departamento en Prados Churubusco",
      },
      {
          "id": "I24-other",
          "source": "inmuebles24",
          "listing_mode": "sale",
          "state": "CDMX",
          "city": "Benito Juárez",
          "neighborhood": "Portales Norte",
          "price_mxn": 4_000_000,
          "type": "departamento",
          "description": "cerca de Av. Rio Churubusco",
      },
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "sale",
            "state": "CDMX",
            "colonia": "Prados Churubusco",
            "municipality": "Coyoacán",
            "neighborhood": "Prados Churubusco",
            "type": "casa",
            "price_max_mxn": 4_900_000,
        },
        query="casa en venta Prados Churubusco CDMX 4900000",
        pilot_states=None,
    )
    assert out == []
    assert "type" not in relax
