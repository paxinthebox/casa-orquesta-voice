"""
P1.2 verification — datasets.py ported correctly from the MVP.

Asserts:
  - The module imports cleanly.
  - rpp_lookup, catastro_lookup, inegi_zone_stats, sat_rfc_check,
    review_text_for_clauses all return non-empty dicts for sample inputs.
  - The two compliance constants (PROMESA_REQUIRED_CLAUSES,
    NOM247_REQUIRED_DISCLOSURES) are non-empty lists.
  - Outputs are deterministic per input (same input → same output).

Run from repo root either way:
    cd services/orchestrator && python3 -m pytest tests/test_datasets.py -v
    cd services/orchestrator && python3 tests/test_datasets.py     # no pytest needed

The self-contained `__main__` block at the bottom matches the MVP convention
(test_agents.py runs without pytest). pytest is the primary path; the standalone
runner exists so the gate works in sandboxed envs too.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

# Deterministic mock assertions — live connectors are tested separately.
os.environ.setdefault("REGISTRY_MODE", "mock")

try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False

    # Minimal pytest compat shim so the decorators don't crash standalone.
    class _PytestStub:
        class mark:
            @staticmethod
            def parametrize(_argstr, argvalues):
                def deco(fn):
                    fn.__parametrize__ = (_argstr, list(argvalues))
                    return fn
                return deco
    pytest = _PytestStub()  # type: ignore

import datasets  # noqa: E402


# ---------------------------------------------------------------------
# Module shape
# ---------------------------------------------------------------------
def test_module_imports_cleanly():
    assert hasattr(datasets, "rpp_lookup")
    assert hasattr(datasets, "catastro_lookup")
    assert hasattr(datasets, "inegi_zone_stats")
    assert hasattr(datasets, "sat_rfc_check")
    assert hasattr(datasets, "review_text_for_clauses")
    assert hasattr(datasets, "PROMESA_REQUIRED_CLAUSES")
    assert hasattr(datasets, "NOM247_REQUIRED_DISCLOSURES")


def test_compliance_constants_non_empty():
    assert isinstance(datasets.PROMESA_REQUIRED_CLAUSES, list)
    assert len(datasets.PROMESA_REQUIRED_CLAUSES) > 0
    assert isinstance(datasets.NOM247_REQUIRED_DISCLOSURES, list)
    assert len(datasets.NOM247_REQUIRED_DISCLOSURES) > 0


# ---------------------------------------------------------------------
# Per-lookup happy paths
# ---------------------------------------------------------------------
def test_rpp_lookup_returns_expected_shape():
    out = datasets.rpp_lookup("CDMX", "Querétaro 123, Roma Norte")
    assert isinstance(out, dict) and out
    for key in ("folio_real", "state", "address", "registered_owner",
                "registration_date", "last_inscripcion", "encumbrances",
                "status", "verification_token"):
        assert key in out, f"missing key '{key}' in rpp_lookup output"
    assert out["state"] == "CDMX"
    assert out["folio_real"].startswith("FOL-CDM-")
    assert isinstance(out["encumbrances"], list)


def test_rpp_lookup_honors_owner_hint():
    out = datasets.rpp_lookup("Morelos", "Tabachines 10", owner_hint="HERNÁNDEZ LÓPEZ, JUAN")
    assert out["registered_owner"] == "HERNÁNDEZ LÓPEZ, JUAN"


def test_catastro_lookup_returns_expected_shape():
    out = datasets.catastro_lookup("Morelos", "Tabachines 10, Cuernavaca")
    assert isinstance(out, dict) and out
    for key in ("clave_catastral", "uso_de_suelo", "valor_catastral_mxn",
                "valor_avaluo_mxn", "impuesto_predial_anual_mxn",
                "ultimo_pago_anio", "al_corriente", "verification_token"):
        assert key in out
    assert out["clave_catastral"].startswith("CAT-MOR-")
    assert isinstance(out["al_corriente"], bool)
    assert out["valor_avaluo_mxn"] > out["valor_catastral_mxn"]


def test_inegi_zone_stats_returns_expected_shape():
    out = datasets.inegi_zone_stats(19.4153, -99.1654)
    assert isinstance(out, dict) and out
    for key in ("ageb_id", "population", "median_household_income_mxn",
                "education_level_pct", "schools_within_1km",
                "crime_index_2025", "verification_token"):
        assert key in out
    assert out["ageb_id"].startswith("AGEB-")
    assert isinstance(out["education_level_pct"], dict)
    assert 1.0 <= out["crime_index_2025"] <= 5.5


def test_sat_rfc_check_returns_expected_shape():
    out = datasets.sat_rfc_check("CAS990101AAA")
    assert isinstance(out, dict) and out
    for key in ("rfc", "registered", "estatus_padron", "regimen_fiscal",
                "domicilio_fiscal_cp", "lista_69", "lista_efos",
                "verification_token"):
        assert key in out
    assert out["rfc"] == "CAS990101AAA"
    assert out["registered"] is True
    assert isinstance(out["lista_69"], bool)
    assert isinstance(out["lista_efos"], bool)


def test_sat_rfc_check_normalizes_case_and_whitespace():
    out = datasets.sat_rfc_check("  cas990101aaa  ")
    assert out["rfc"] == "CAS990101AAA"


# ---------------------------------------------------------------------
# Clause reviewer
# ---------------------------------------------------------------------
def test_clause_reviewer_full_text_scores_1():
    text = ("objeto plazo días naturales anticipo pena convencional lfpdppp "
            "nom-151 jurisdicción tribunales")
    out = datasets.review_text_for_clauses(text, datasets.PROMESA_REQUIRED_CLAUSES)
    assert out["score"] == 1.0
    assert out["missing"] == []


def test_clause_reviewer_partial_text_scores_below_1():
    text = "solo objeto y plazo aquí"
    out = datasets.review_text_for_clauses(text, datasets.PROMESA_REQUIRED_CLAUSES)
    assert 0.0 < out["score"] < 1.0
    assert len(out["missing"]) > 0


def test_clause_reviewer_empty_required_scores_1():
    out = datasets.review_text_for_clauses("cualquier texto", [])
    assert out["score"] == 1.0
    assert out["missing"] == []


# ---------------------------------------------------------------------
# Determinism — same input → same output
# ---------------------------------------------------------------------
@pytest.mark.parametrize("state,address", [
    ("CDMX", "Querétaro 123"),
    ("Morelos", "Tabachines 10"),
    ("Morelos", "Galeana 5, Centro"),
])
def test_rpp_lookup_deterministic(state, address):
    a = datasets.rpp_lookup(state, address)
    b = datasets.rpp_lookup(state, address)
    assert a["folio_real"] == b["folio_real"]
    assert a["status"] == b["status"]
    assert a["verification_token"] == b["verification_token"]


def test_catastro_lookup_deterministic():
    a = datasets.catastro_lookup("CDMX", "Pestalozzi 5")
    b = datasets.catastro_lookup("CDMX", "Pestalozzi 5")
    assert a["clave_catastral"] == b["clave_catastral"]
    assert a["valor_catastral_mxn"] == b["valor_catastral_mxn"]


def test_inegi_zone_stats_deterministic():
    a = datasets.inegi_zone_stats(19.4153, -99.1654)
    b = datasets.inegi_zone_stats(19.4153, -99.1654)
    assert a["ageb_id"] == b["ageb_id"]
    assert a["population"] == b["population"]


def test_sat_rfc_check_deterministic():
    a = datasets.sat_rfc_check("XAXX010101000")
    b = datasets.sat_rfc_check("XAXX010101000")
    assert a["lista_69"] == b["lista_69"]
    assert a["lista_efos"] == b["lista_efos"]
    assert a["verification_token"] == b["verification_token"]


# ---------------------------------------------------------------------
# Standalone runner — matches MVP test_agents.py convention.
# Runs every test_* function (and expands @pytest.mark.parametrize) when
# pytest isn't available. Same green/red signal, no external dependency.
# ---------------------------------------------------------------------
def _standalone_main() -> int:
    import traceback
    passed: list[str] = []
    failed: list[tuple[str, str]] = []
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]

    print("=" * 70)
    print(f"  P1.2 — datasets.py port verification ({len(tests)} test functions)")
    print("=" * 70)

    for name, fn in tests:
        param = getattr(fn, "__parametrize__", None)
        if param is None and HAS_PYTEST:
            # Real pytest decorators store marks in `pytestmark`; mirror
            # them into the shim shape so the standalone runner works
            # whether or not pytest is installed.
            for mark in getattr(fn, "pytestmark", []):
                if mark.name == "parametrize":
                    param = (mark.args[0], mark.args[1])
                    break
        cases: list[tuple[tuple, str]] = [((), "")]
        if param:
            argstr, values = param
            argnames = [a.strip() for a in argstr.split(",")]
            cases = []
            for v in values:
                vals = v if isinstance(v, tuple) else (v,)
                label = "[" + ",".join(f"{n}={r}" for n, r in zip(argnames, vals)) + "]"
                cases.append((vals, label))

        for args, label in cases:
            full = f"{name}{label}"
            try:
                fn(*args)
                passed.append(full)
                print(f"  ✅ {full}")
            except Exception as e:
                failed.append((full, repr(e)))
                print(f"  ❌ {full}  ← {e!r}")
                if os.getenv("VERBOSE"):
                    traceback.print_exc()

    print()
    print("=" * 70)
    print(f"  SUMMARY  passed={len(passed)}  failed={len(failed)}")
    print("=" * 70)
    if failed:
        for name, err in failed:
            print(f"  ❌ {name}: {err}")
        return 1
    print("  All datasets port assertions green. ✅")
    return 0


if __name__ == "__main__":
    sys.exit(_standalone_main())
