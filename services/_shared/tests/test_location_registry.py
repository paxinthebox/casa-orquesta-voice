"""Location hierarchy registry tests."""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _SHARED)

from location_registry import resolve_location  # noqa: E402
from location_filters import (  # noqa: E402
    extract_location_filters,
    listing_matches_location,
    location_needles,
)


def test_estado_morelos():
    f = resolve_location("propiedades en Morelos").to_filters()
    assert f["state"] == "Morelos"
    assert f["location_level"] == "estado"


def test_municipio_jiutepec():
    f = resolve_location("casa en Jiutepec, Morelos").to_filters()
    assert f["state"] == "Morelos"
    assert f["municipality"] == "Jiutepec"
    assert f["city"] == "Jiutepec"
    assert f["location_level"] == "municipio"


def test_colonia_polanco_municipio():
    f = resolve_location("departamento en Polanco CDMX").to_filters()
    assert f["state"] == "CDMX"
    assert f["colonia"] == "Polanco"
    assert f["municipality"] == "Miguel Hidalgo"
    assert f["location_level"] == "colonia"


def test_fraccionamiento_burgos():
    f = resolve_location("casa en Fraccionamiento Burgos Bugambilias, Temixco").to_filters()
    assert f["fraccionamiento"] == "Burgos Bugambilias"
    assert f["municipality"] == "Temixco"
    assert f["state"] == "Morelos"
    assert f["location_level"] == "fraccionamiento"


def test_fraccionamiento_club_golf_santafe():
    f = extract_location_filters("Busco casa en venta en Club de Golf Santafe")
    assert f["state"] == "CDMX"
    assert f["fraccionamiento"] == "Club de Golf Santa Fe"
    assert f["municipality"] == "Álvaro Obregón"


def test_colonia_labeled():
    f = extract_location_filters("depto en Col. Del Valle, Benito Juárez")
    assert f["colonia"] == "Del Valle"
    assert f["municipality"] == "Benito Juárez"


def test_localidad_pueblo_chiconcuac():
    f = extract_location_filters("casa en Pueblo Chiconcuac, Xochitepec")
    assert f["locality"] == "Pueblo Chiconcuac"
    assert f["municipality"] == "Xochitepec"


def test_zone_norte_de_cuernavaca():
    f = extract_location_filters(
        "Casa en venta al norte de Cuernavaca presupuesto máximo de 2300000",
    )
    assert f["zone"] == "norte de cuernavaca"
    assert f["city"] == "Cuernavaca"
    assert f["state"] == "Morelos"
    assert f["location_level"] == "zone"
    assert "neighborhood" not in f


def test_zone_filter_includes_vista_hermosa_not_only_lomas():
    from location_filters import filter_by_location  # noqa: E402

    rows = [
        {
            "title": "Casa en Lomas de Cortés",
            "city": "Cuernavaca",
            "state": "Morelos",
            "neighborhood": "Lomas de Cortés",
        },
        {
            "title": "Casa en Vista Hermosa",
            "city": "Cuernavaca",
            "state": "Morelos",
            "neighborhood": "Vista Hermosa",
        },
        {
            "title": "Casa en Centro Cuernavaca",
            "city": "Cuernavaca",
            "state": "Morelos",
            "neighborhood": "Centro",
            "description": "Zona céntrica sur",
        },
    ]
    filt = {"zone": "norte de cuernavaca", "city": "Cuernavaca", "state": "Morelos"}
    out = filter_by_location(rows, filters=filt, query="norte de cuernavaca")
    hoods = {r["neighborhood"] for r in out}
    assert "Lomas de Cortés" in hoods
    assert "Vista Hermosa" in hoods
    assert "Centro" not in hoods


def test_extension_from_pilot_places_json():
    f = extract_location_filters("casa en Fraccionamiento Sumiya, Jiutepec")
    assert f.get("fraccionamiento") == "Sumiya"
    assert f.get("municipality") == "Jiutepec"


def test_colonia_prados_churubusco():
    f = extract_location_filters(
        "casa en venta en Prados Churubusco CDMX presupuesto 4900000",
    )
    assert f["colonia"] == "Prados Churubusco"
    assert f["municipality"] == "Coyoacán"
    assert f["state"] == "CDMX"
    assert f["location_level"] == "colonia"
    needles = location_needles(filters=f, query="Prados Churubusco")
    wrong = {
        "title": "Departamento en Portales Norte",
        "description": "cerca de Av. Rio Churubusco",
        "neighborhood": "Portales Norte",
        "city": "Benito Juárez",
    }
    assert not listing_matches_location(wrong, needles)


def test_fracc_needles_exclude_generic_colonia():
    needles = location_needles(
        filters={"fraccionamiento": "Club de Golf Santa Fe", "state": "CDMX"},
        query="casa Club de Golf Santafe",
    )
    rent_row = {
        "title": "Departamento en Renta en Tres Cumbres Santa Fe",
        "neighborhood": "Santa Fe",
        "city": "Alvaro Obregón",
    }
    assert not listing_matches_location(rent_row, needles)
