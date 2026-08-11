"""Live RPP lookups — broker URL or catastro cross-reference + portal guidance."""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from registries.config import HTTP_TIMEOUT_S, RPP_LOOKUP_URL
from registries.catastro_live import catastro_lookup
from registries.geocode import geocode_address


def _broker_lookup(state: str, address: str, owner_hint: str | None) -> dict[str, Any] | None:
    if not RPP_LOOKUP_URL:
        return None
    payload = {"state": state, "address": address, "owner_hint": owner_hint}
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True) as client:
            resp = client.post(RPP_LOOKUP_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("source", "live")
    data.setdefault("provider", "rpp_broker")
    return data


def rpp_lookup(state: str, address: str, owner_hint: str | None = None) -> dict[str, Any] | None:
    broker = _broker_lookup(state, address, owner_hint)
    if broker:
        return broker

    geo = geocode_address(address, state)
    cat = catastro_lookup(state, address)
    digest = hashlib.sha1(f"rpp:{state}:{address}".encode()).hexdigest()[:8].upper()

    portal = (
        "https://servicios.consejeria.cdmx.gob.mx/cmscejur/dgrppc/servicios-electronicos/"
        if state.upper() in ("CDMX", "CIUDAD DE MÉXICO", "CIUDAD DE MEXICO", "DF")
        else None
    )

    return {
        "folio_real": None,
        "state": state,
        "address": address,
        "registered_owner": owner_hint,
        "owner_curp_partial": None,
        "registration_date": None,
        "last_inscripcion": None,
        "encumbrances": [],
        "status": "requiere_consulta_oficial",
        "geocode": geo,
        "catastro_cross_ref": {
            "clave_catastral": (cat or {}).get("clave_catastral"),
            "source": (cat or {}).get("source"),
        } if cat else None,
        "note": (
            "No hay API pública nacional de RPP. Casa·Orquesta geocodificó la dirección "
            "y enlazó catastro cuando fue posible. Para folio real y gravámenes, "
            "consulta el registro estatal o configura RPP_LOOKUP_URL con tu proveedor."
        ),
        "portal_url": portal,
        "verification_token": f"RPP-LIVE-{digest}",
        "source": "live_partial",
        "provider": "geocode+catastro",
    }
