"""Near-match must not widen Cuernavaca queries into CDMX."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(SVC, "..", "_shared"))
sys.path.insert(0, SVC)
sys.path.insert(0, SHARED)

from location_filters import row_in_locality_scope  # noqa: E402
from near_match import _scoped_pilot_states, find_near_matches  # noqa: E402


def test_scoped_pilot_states_drops_cdmx_for_cuernavaca():
    out = _scoped_pilot_states(
        ["CDMX", "Morelos"],
        filters={},
        query="casa en renta en cuernavaca 16000",
    )
    assert out == ["Morelos"]


def test_row_in_locality_scope_rejects_cdmx_for_cuernavaca():
    row = {"id": "I24-cdmx", "state": "CDMX", "city": "Ciudad de México", "title": "Roma"}
    assert not row_in_locality_scope(
        row,
        filters={"state": "Morelos", "city": "Cuernavaca"},
        query="renta cuernavaca",
    )


def test_near_match_fill_stays_in_morelos():
    rows = [
        {"id": "1", "state": "Morelos", "city": "Cuernavaca", "price_mxn": 13800,
         "listing_mode": "rent", "title": "Cuernavaca"},
        {"id": "2", "state": "Morelos", "city": "Cuernavaca", "price_mxn": 20000,
         "listing_mode": "rent", "title": "Cuernavaca norte"},
        {"id": "3", "state": "CDMX", "city": "Ciudad de México", "price_mxn": 22000,
         "listing_mode": "rent", "title": "CDMX Roma"},
    ]
    out, relax = find_near_matches(
        rows,
        filters={
            "listing_mode": "rent",
            "states": ["CDMX", "Morelos"],
            "state": "Morelos",
            "city": "Cuernavaca",
            "price_max_mxn": 16000,
        },
        query="casa en renta cuernavaca 16000",
        pilot_states=["Morelos"],
        limit=5,
    )
    assert all(r["state"] == "Morelos" for r in out)
    assert "3" not in {r["id"] for r in out}
