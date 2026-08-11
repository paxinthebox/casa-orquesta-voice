"""Tests for es-MX spoken money normalization."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, SHARED)

from money_speech import (  # noqa: E402
    extract_price_max_mxn,
    format_mxn,
    normalize_money_in_text,
)


def test_format_mxn_monthly():
    assert format_mxn(40000, monthly=True) == "$40,000/mes"
    assert format_mxn(8000000) == "$8,000,000"


def test_normalize_spoken_thousands():
    out = normalize_money_in_text("Busco renta hasta cuarenta mil al mes")
    assert "$40,000/mes" in out
    assert "cuarenta mil" not in out


def test_normalize_spoken_millions_sale():
    out = normalize_money_in_text("Casa en venta por ocho millones")
    assert "$8,000,000" in out


def test_normalize_treinta_y_cinco_mil():
    out = normalize_money_in_text("Presupuesto de treinta y cinco mil pesos al mes")
    assert "$35,000/mes" in out


def test_rent_price_from_words():
    assert extract_price_max_mxn(
        "departamento en renta hasta cuarenta mil al mes",
        listing_mode="rent",
    ) == 40000


def test_rent_ignores_bare_millones():
    assert extract_price_max_mxn(
        "departamento en renta en Polanco por ocho millones",
        listing_mode="rent",
    ) is None


def test_sale_price_millones():
    assert extract_price_max_mxn(
        "casa en venta hasta 8 millones",
        listing_mode="sale",
    ) == 8_000_000


def test_sale_price_millones_mil_compound():
    assert extract_price_max_mxn(
        "casa en venta hasta 4 millones novecientos mil pesos",
        listing_mode="sale",
    ) == 4_900_000
    assert extract_price_max_mxn(
        "cuatro millones novecientos mil",
        listing_mode="sale",
    ) == 4_900_000
    assert extract_price_max_mxn(
        "4 millones 900 mil",
        listing_mode="sale",
    ) == 4_900_000


def test_rent_price_from_pesos_al_mes():
    assert extract_price_max_mxn(
        "hasta 35,000 pesos al mes",
        listing_mode="rent",
    ) == 35_000


def test_sale_ignores_budget_analysis_dollar_amounts():
    prompt = (
        "Busco propiedades para un cliente hasta 8,000,000 pesos "
        "Análisis de presupuesto: valor de propiedad $7,600,000 MXN, "
        "notaría $380,000."
    )
    assert extract_price_max_mxn(prompt, listing_mode="sale") == 8_000_000
