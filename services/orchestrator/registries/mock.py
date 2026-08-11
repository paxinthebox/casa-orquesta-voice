"""Deterministic mock registry responses (MVP parity)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any


def rpp_lookup(state: str, address: str, owner_hint: str | None = None) -> dict[str, Any]:
    hash_id = hashlib.sha1(f"{state}:{address}".encode()).hexdigest()[:8].upper()
    folio = f"FOL-{state[:3].upper()}-{hash_id}"
    deterministic = int(hash_id, 16) % 100
    is_clean = deterministic > 12

    base: dict[str, Any] = {
        "folio_real": folio,
        "state": state,
        "address": address,
        "registered_owner": owner_hint or "RAMÍREZ MARTÍNEZ, MARÍA ELENA",
        "owner_curp_partial": "RAMM75****HDFNRR**",
        "registration_date": (datetime.now() - timedelta(days=2400 + deterministic * 7)).date().isoformat(),
        "last_inscripcion": (datetime.now() - timedelta(days=120 + deterministic)).date().isoformat(),
        "encumbrances": [],
        "status": "vigente",
        "verification_token": f"RPP-MOCK-{folio}",
        "source": "mock",
    }

    if not is_clean:
        base["encumbrances"].append({
            "type": "hipoteca",
            "creditor": "Banco BBVA Bancomer, S.A.",
            "amount_mxn": 1_400_000 + deterministic * 5000,
            "registered_at": (datetime.now() - timedelta(days=400)).date().isoformat(),
            "status": "vigente",
        })
        base["status"] = "con_gravamen"

    return base


def catastro_lookup(state: str, address: str) -> dict[str, Any]:
    hash_id = hashlib.sha1(f"catastro:{state}:{address}".encode()).hexdigest()[:8].upper()
    deterministic = int(hash_id[:4], 16) % 100
    impuesto_predial_year = (deterministic * 175) + 850
    last_paid_year = 2025 if deterministic > 8 else 2024

    return {
        "clave_catastral": f"CAT-{state[:3].upper()}-{hash_id}",
        "uso_de_suelo": "habitacional unifamiliar" if deterministic > 30 else "habitacional plurifamiliar",
        "valor_catastral_mxn": 2_500_000 + deterministic * 25_000,
        "valor_avaluo_mxn": int((2_500_000 + deterministic * 25_000) * 1.45),
        "impuesto_predial_anual_mxn": impuesto_predial_year,
        "ultimo_pago_anio": last_paid_year,
        "al_corriente": last_paid_year >= 2025,
        "verification_token": f"CAT-MOCK-{hash_id}",
        "source": "mock",
    }


def inegi_zone_stats(lat: float, lng: float) -> dict[str, Any]:
    hash_id = hashlib.sha1(f"{round(lat, 3)}:{round(lng, 3)}".encode()).hexdigest()
    seed = int(hash_id[:6], 16)

    return {
        "ageb_id": f"AGEB-{hash_id[:6].upper()}",
        "population": 1200 + (seed % 8000),
        "median_household_income_mxn": 18_000 + (seed % 60_000),
        "education_level_pct": {
            "secundaria_o_menos": (seed % 25),
            "preparatoria": 25 + (seed % 20),
            "licenciatura_o_mas": 40 + (seed % 30),
        },
        "schools_within_1km": 3 + (seed % 8),
        "metro_distance_m": 200 + (seed % 1500) if (seed % 3 == 0) else None,
        "crime_index_2025": round(2.1 + (seed % 10) / 3, 1),
        "verification_token": f"INEGI-MOCK-{hash_id[:8]}",
        "source": "mock",
    }


def sat_rfc_check(rfc: str) -> dict[str, Any]:
    rfc = rfc.strip().upper()
    digest = hashlib.sha1(rfc.encode()).hexdigest()
    flag = int(digest[:2], 16) % 100
    on_efos = flag < 1
    on_69 = flag < 2

    return {
        "rfc": rfc,
        "registered": True,
        "estatus_padron": "activo",
        "regimen_fiscal": (
            "601 — General de Ley Personas Morales"
            if rfc.startswith("CAS")
            else "612 — Personas Físicas con Actividades Empresariales"
        ),
        "domicilio_fiscal_cp": "06600",
        "lista_69": on_69,
        "lista_efos": on_efos,
        "verification_token": f"SAT-MOCK-{digest[:8]}",
        "source": "mock",
    }
