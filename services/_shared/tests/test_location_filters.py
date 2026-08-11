"""Tests for strict city/region filtering."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, SHARED)

from location_filters import (  # noqa: E402
    extract_location_filters,
    filter_by_location,
    filter_by_state,
)


def _row(city: str, **extra) -> dict:
    return {"id": extra.get("id", "X"), "city": city, "state": "Morelos", **extra}


def test_extract_xochitepec():
    f = extract_location_filters("Busco departamento en Xochitepec")
    assert f["city"] == "Xochitepec"
    assert f["state"] == "Morelos"


def test_strict_city_excludes_other_municipalities():
    candidates = [
        _row("Cuernavaca", id="1"),
        _row("Jiutepec", id="2"),
        _row("Xochitepec", id="3", neighborhood="Centro"),
    ]
    out = filter_by_location(
        candidates,
        filters={"city": "Xochitepec", "state": "Morelos"},
        query="departamento en Xochitepec",
    )
    assert len(out) == 1
    assert out[0]["id"] == "3"


def test_no_city_keeps_statewide_pool():
    candidates = [_row("Cuernavaca"), _row("Jiutepec")]
    out = filter_by_location(
        candidates,
        filters={"state": "Morelos"},
        query="casas en Morelos",
    )
    assert len(out) == 2


def test_filter_by_state_morelos():
    rows = [
        {"id": "1", "state": "CDMX", "city": "CDMX"},
        {"id": "2", "state": "Morelos", "city": "Cuernavaca"},
    ]
    out = filter_by_state(rows, "Morelos")
    assert len(out) == 1
    assert out[0]["id"] == "2"


def test_empty_when_city_has_no_inventory():
    candidates = [_row("Cuernavaca"), _row("Jiutepec")]
    out = filter_by_location(
        candidates,
        filters={"city": "Xochitepec", "state": "Morelos"},
        query="departamento en Xochitepec",
    )
    assert out == []


def test_condesa_excludes_other_cdmx_colonias():
    candidates = [
        {
            "id": "1",
            "state": "CDMX",
            "city": "CDMX",
            "neighborhood": "Condesa",
            "address": "Av. Amsterdam, Condesa",
        },
        {
            "id": "2",
            "state": "CDMX",
            "city": "CDMX",
            "neighborhood": "Roma Norte",
            "address": "Querétaro, Roma Norte",
        },
        {
            "id": "3",
            "state": "CDMX",
            "city": "CDMX",
            "neighborhood": "Polanco",
            "address": "Masaryk, Polanco",
        },
    ]
    out = filter_by_location(
        candidates,
        filters={"city": "Ciudad de México", "state": "CDMX", "neighborhood": "condesa"},
        query="departamento en Condesa, CDMX",
    )
    assert len(out) == 1
    assert out[0]["id"] == "1"


def test_condesa_ignores_marketing_copy_in_description():
    """Portal blurbs that name-drop Condesa must not count as being in Condesa."""
    candidates = [
        {
            "id": "roma",
            "state": "CDMX",
            "city": "Cuauhtémoc",
            "neighborhood": "Roma Norte",
            "title": "Departamento en Venta en Roma Norte",
            "address": "Ciudad de México, Cuauhtémoc, CDMX",
            "description": "A solo pasos de Condesa, Escandon y el WTC.",
        },
        {
            "id": "condesa",
            "state": "CDMX",
            "city": "Cuauhtémoc",
            "neighborhood": "Condesa",
            "title": "Departamento en Condesa",
            "address": "Amsterdam, Condesa",
        },
    ]
    out = filter_by_location(
        candidates,
        filters={
            "state": "CDMX",
            "municipality": "Cuauhtémoc",
            "colonia": "Condesa",
            "neighborhood": "Condesa",
        },
        query="departamento Condesa",
    )
    assert [r["id"] for r in out] == ["condesa"]


def test_extract_condesa_sets_neighborhood():
    f = extract_location_filters("Condesa")
    assert f["state"] == "CDMX"
    assert f["city"] == "Ciudad de México"
    assert f["colonia"] == "Condesa"
    assert f["municipality"] == "Cuauhtémoc"


def test_extract_santafe_one_word():
    f = extract_location_filters("Busco casa en venta en Club de Golf Santafe")
    assert f["state"] == "CDMX"
    assert f["fraccionamiento"] == "Club de Golf Santa Fe"
    assert f["municipality"] == "Álvaro Obregón"


def test_extract_club_de_golf_santa_fe():
    f = extract_location_filters("casa en Club de Golf Santa Fe")
    assert f["state"] == "CDMX"
    assert f["fraccionamiento"] == "Club de Golf Santa Fe"


def test_club_de_golf_needles_exclude_generic_santa_fe_colonia():
    from location_filters import listing_matches_location, location_needles

    needles = location_needles(
        filters={"fraccionamiento": "Club de Golf Santa Fe", "state": "CDMX"},
        query="Busco casa en venta en Club de Golf Santafe",
    )
    rent_row = {
        "title": "Departamento en Renta en Tres Cumbres Santa Fe",
        "neighborhood": "Santa Fe",
        "city": "Alvaro Obregón",
    }
    assert not listing_matches_location(rent_row, needles)
