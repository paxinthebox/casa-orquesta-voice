"""Live registry connector tests (SAT download + geocode; no mock mode)."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

# Force live for this module only.
os.environ["REGISTRY_MODE"] = "live"


def test_sat_live_known_list_shape():
    from registries import sat_live

    out = sat_live.sat_rfc_check("ZZZZ999999ZZZ")
    assert out["source"] == "live"
    assert "lista_69" in out
    assert "lista_efos" in out


def test_geocode_cdmx_address():
    from registries.geocode import geocode_address

    geo = geocode_address("Querétaro 123, Roma Norte", "CDMX")
    assert geo is not None
    assert 19.0 < geo["lat"] < 20.0
    assert -100.0 < geo["lng"] < -98.0


def test_catastro_live_partial_geocode():
    from registries import catastro_live

    out = catastro_live.catastro_lookup("CDMX", "Querétaro 123, Roma Norte")
    assert out is not None
    assert out["source"] in ("live", "live_partial")
    assert out.get("geocode")
