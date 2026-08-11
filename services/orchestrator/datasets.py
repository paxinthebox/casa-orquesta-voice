"""
Mock public datasets for the audit_agent.

Registry lookups (RPP, Catastro, INEGI, SAT) delegate to `registries/`
which tries live connectors when REGISTRY_MODE=auto|live, then falls back
to deterministic mocks for stage/demo.

Compliance clause library remains here unchanged.
"""
from __future__ import annotations

from typing import Any

from registries import (
    catastro_lookup as _registry_catastro_lookup,
    inegi_zone_stats as _registry_inegi_zone_stats,
    rpp_lookup as _registry_rpp_lookup,
    sat_rfc_check as _registry_sat_rfc_check,
)


def rpp_lookup(state: str, address: str, owner_hint: str | None = None) -> dict[str, Any]:
    return _registry_rpp_lookup(state, address, owner_hint)


def catastro_lookup(state: str, address: str) -> dict[str, Any]:
    return _registry_catastro_lookup(state, address)


def inegi_zone_stats(lat: float, lng: float) -> dict[str, Any]:
    return _registry_inegi_zone_stats(lat, lng)


def sat_rfc_check(rfc: str) -> dict[str, Any]:
    return _registry_sat_rfc_check(rfc)


# ---------------- Compliance rule library (for audit_agent) ----------------
PROMESA_REQUIRED_CLAUSES = [
    "objeto",
    "plazo",
    "anticipo",
    "pena_convencional",
    "datos_personales_lfpdppp",
    "firma_electronica_nom151",
    "jurisdiccion",
]

NOM247_REQUIRED_DISCLOSURES = [
    "precio_total",
    "superficie",
    "ubicacion_clara",
    "responsable_anuncio",
]


def review_text_for_clauses(text: str, required: list[str]) -> dict:
    """Heuristic clause detector. Production: a fine-tuned NER classifier."""
    text_low = text.lower()
    findings = []
    keywords = {
        "objeto": ["objeto", "compraventa", "promesa"],
        "plazo": ["plazo", "días naturales", "días hábiles", "fecha límite"],
        "anticipo": ["anticipo", "enganche", "depósito", "pago inicial"],
        "pena_convencional": ["pena convencional", "incumplimiento", "10%"],
        "datos_personales_lfpdppp": ["lfpdppp", "datos personales", "aviso de privacidad"],
        "firma_electronica_nom151": ["nom-151", "nom 151", "mifiel", "firma electrónica avanzada"],
        "jurisdiccion": ["jurisdicción", "tribunales", "renunciando a cualquier otro fuero"],
        "precio_total": ["precio total", "monto total", "$"],
        "superficie": ["superficie", "m²", "metros cuadrados"],
        "ubicacion_clara": ["ubicación", "calle", "colonia", "código postal"],
        "responsable_anuncio": ["responsable", "anunciante", "agente"],
    }
    for clause in required:
        kws = keywords.get(clause, [clause])
        present = any(kw in text_low for kw in kws)
        findings.append({
            "clause": clause,
            "present": present,
            "matched_terms": [kw for kw in kws if kw in text_low],
        })
    missing = [f["clause"] for f in findings if not f["present"]]
    return {
        "required": required,
        "findings": findings,
        "missing": missing,
        "score": round((len(required) - len(missing)) / len(required), 2) if required else 1.0,
    }
