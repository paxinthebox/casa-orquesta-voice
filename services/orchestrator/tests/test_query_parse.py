"""Tests for natural-language filter extraction."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

from query_parse import extract_filters  # noqa: E402


def test_sale_apartment():
    f = extract_filters("Busco un departamento en venta en Roma Norte")
    assert f["type"] == "departamento"
    assert f["listing_mode"] == "sale"
    assert f["state"] == "CDMX"


def test_long_term_rent_condo():
    f = extract_filters("Quiero un condo en renta anual en Cuernavaca hasta 40 mil")
    assert f["type"] == "condominio"
    assert f["listing_mode"] == "rent"
    assert f["state"] == "Morelos"
    assert f["price_max_mxn"] == 40000


def test_rent_spoken_budget_words():
    f = extract_filters("Busco departamento en renta en Roma hasta cuarenta mil al mes")
    assert f["listing_mode"] == "rent"
    assert f["price_max_mxn"] == 40000


def test_rent_ignores_sale_millones():
    f = extract_filters("departamento en renta en Polanco hasta ocho millones")
    assert f["listing_mode"] == "rent"
    assert "price_max_mxn" not in f


def test_sale_millones():
    f = extract_filters("Busco casa en venta en Condesa hasta ocho millones")
    assert f["listing_mode"] == "sale"
    assert f["price_max_mxn"] == 8_000_000
    assert f["neighborhood"] == "Condesa"


def test_house_rent():
    f = extract_filters("Necesito una casa en alquiler en CDMX")
    assert f["type"] == "casa"
    assert f["listing_mode"] == "rent"


def test_short_term_not_long_rent():
    f = extract_filters("departamento vacacional en renta por noche")
    assert f.get("listing_mode") != "rent"


def test_xochitepec_apartment():
    f = extract_filters("Busco un departamento en venta en Xochitepec")
    assert f["type"] == "departamento"
    assert f["listing_mode"] == "sale"
    assert f["city"] == "Xochitepec"
    assert f["state"] == "Morelos"


def test_cuernavaca_norte_rent_casa_o_departamento():
    msg = (
        "Casa o departamento en renta al norte de cuernavaca con "
        "3 lugares de estacionamiento y un presupuesto de 16,000 pesos mensuales"
    )
    f = extract_filters(msg)
    assert f["listing_mode"] == "rent"
    assert f["price_max_mxn"] == 16000
    assert f["types"] == ["departamento", "casa"]
    assert f["state"] == "Morelos"
    assert f["city"] == "Cuernavaca"
    assert f["zone"] == "norte de cuernavaca"
    assert f["location_level"] == "zone"
    assert "neighborhood" not in f or f.get("neighborhood") != "Lomas de Cortés"
    assert "estacionamiento" in f.get("features", [])


def test_club_de_golf_santafe_sale_casa_budget():
    f = extract_filters("Busco casa en venta en Club de Golf Santafe en 9,000,000")
    assert f["type"] == "casa"
    assert f["listing_mode"] == "sale"
    assert f["price_max_mxn"] == 9_000_000
    assert f["state"] == "CDMX"
    assert f["fraccionamiento"] == "Club de Golf Santa Fe"


def test_cuernavaca_norte_sale_casa_budget():
    f = extract_filters(
        "Casa en venta al norte de Cuernavaca presupuesto máximo de $2,300,000",
    )
    assert f["type"] == "casa"
    assert f["listing_mode"] == "sale"
    assert f["price_max_mxn"] == 2_300_000
    assert f["state"] == "Morelos"
    assert f["city"] == "Cuernavaca"
    assert f["zone"] == "norte de cuernavaca"
    assert "neighborhood" not in f


def test_prados_churubusco_casa_sale():
    f = extract_filters(
        "Busco una casa en venta en Prados Churubusco CDMX con presupuesto de 4,900,000",
    )
    assert f["type"] == "casa"
    assert f["listing_mode"] == "sale"
    assert f["price_max_mxn"] == 4_900_000
    assert f["colonia"] == "Prados Churubusco"
    assert f["municipality"] == "Coyoacán"


def test_english_house_maps_to_casa():
    f = extract_filters("house for sale in Prados Churubusco CDMX budget 4900000")
    assert f["type"] == "casa"
    assert f["colonia"] == "Prados Churubusco"


def test_elevador_seguridad_and_baths():
    f = extract_filters(
        "departamento en venta en Polanco con 2 baños, elevador y seguridad",
    )
    assert f.get("baths_min") == 2
    feats = f.get("features") or []
    assert "elevador" in feats
    assert "seguridad" in feats


def test_formulary_rent_pesos_al_mes():
    f = extract_filters(
        "Busco propiedades para un cliente en renta anual que busca departamento "
        "en Roma Norte, CDMX hasta 35,000 pesos al mes",
    )
    assert f["listing_mode"] == "rent"
    assert f["price_max_mxn"] == 35_000
