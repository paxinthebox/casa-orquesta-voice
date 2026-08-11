"""Closeness ranking — closest alternatives, not random inventory."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(SVC, "..", "_shared"))
sys.path.insert(0, SVC)
sys.path.insert(0, SHARED)

from closeness import closeness_score  # noqa: E402


def _row(**kwargs) -> dict:
    base = {
        "id": "I24-1",
        "source": "inmuebles24",
        "listing_mode": "rent",
        "state": "Morelos",
        "city": "Cuernavaca",
        "neighborhood": "Vista Hermosa",
        "price_mxn": 20_000,
        "beds": 0,
        "type": "casa",
        "title": "Casa en renta",
        "description": "",
        "features": [],
    }
    base.update(kwargs)
    return base


_FILTERS = {
    "listing_mode": "rent",
    "state": "Morelos",
    "city": "Cuernavaca",
    "zone": "norte de cuernavaca",
    "price_max_mxn": 16_000,
    "types": ["casa", "departamento"],
    "features": ["estacionamiento"],
}

_QUERY = (
    "Casa o departamento en renta al norte de cuernavaca "
    "con 3 lugares de estacionamiento presupuesto 16,000"
)


def test_garage_listing_ranks_above_over_budget_without_parking():
    garage = _row(
        id="I24-150192481",
        price_mxn=13_800,
        neighborhood="Lomas de Cortés",
        description="Garage para 3 autos, amplio jardín",
    )
    expensive = _row(
        id="I24-149058935",
        price_mxn=20_000,
        neighborhood="Vista Hermosa",
        description="Casa amplia sin cochera",
    )
    cdmx = _row(
        id="I24-CDMX",
        state="CDMX",
        city="Ciudad de México",
        neighborhood="Roma",
        price_mxn=14_000,
        description="Departamento con estacionamiento",
    )

    s_garage = closeness_score(garage, filters=_FILTERS, query=_QUERY)
    s_expensive = closeness_score(expensive, filters=_FILTERS, query=_QUERY, relaxations={"price_max_mxn"})
    s_cdmx = closeness_score(cdmx, filters=_FILTERS, query=_QUERY)

    assert s_garage > s_expensive
    assert s_garage > s_cdmx


def test_under_budget_beats_over_budget_when_relaxed():
    under = _row(id="A", price_mxn=15_000)
    over = _row(id="B", price_mxn=21_000)
    relax = {"price_max_mxn"}
    assert closeness_score(under, filters=_FILTERS, query=_QUERY, relaxations=relax) > closeness_score(
        over, filters=_FILTERS, query=_QUERY, relaxations=relax,
    )


def test_cuernavaca_beats_other_morelos_when_city_scoped():
    cuer = _row(id="C", city="Cuernavaca", neighborhood="Centro", price_mxn=15_000)
    jiute = _row(id="J", city="Jiutepec", neighborhood="Centro", price_mxn=14_000)
    filters = {k: v for k, v in _FILTERS.items() if k != "neighborhood"}
    assert closeness_score(cuer, filters=filters, query=_QUERY) > closeness_score(
        jiute, filters=filters, query=_QUERY, relaxations={"location"},
    )
