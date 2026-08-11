"""Live catastro lookups — CDMX WFS + geocode; Morelos geocode partial."""
from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlencode

import httpx

from registries.config import (
    CDMX_CATASTRO_LAYERS,
    CDMX_CATASTRO_WFS_URL,
    HTTP_TIMEOUT_S,
)
from registries.geocode import geocode_address


def _wfs_feature(lat: float, lng: float) -> dict[str, Any] | None:
    delta = 0.0008
    bbox = f"{lng - delta},{lat - delta},{lng + delta},{lat + delta}"
    for layer in CDMX_CATASTRO_LAYERS:
        params = {
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": layer,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "bbox": bbox,
        }
        url = f"{CDMX_CATASTRO_WFS_URL}?{urlencode(params)}"
        try:
            with httpx.Client(timeout=min(HTTP_TIMEOUT_S, 8.0), follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    continue
                data = resp.json()
        except Exception:
            continue
        features = data.get("features") if isinstance(data, dict) else None
        if not features:
            continue
        props = features[0].get("properties") or {}
        if not props:
            continue
        return props
    return None


def _pick(props: dict[str, Any], *keys: str) -> Any:
    lowered = {str(k).lower(): v for k, v in props.items()}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()] not in (None, ""):
            return lowered[key.lower()]
    return None


def catastro_lookup(state: str, address: str) -> dict[str, Any] | None:
    geo = geocode_address(address, state)
    if not geo:
        return None

    lat = geo["lat"]
    lng = geo["lng"]
    digest = hashlib.sha1(f"{state}:{address}:{lat}:{lng}".encode()).hexdigest()[:8].upper()

    if state.upper() in ("CDMX", "CIUDAD DE MÉXICO", "CIUDAD DE MEXICO", "DF"):
        props = _wfs_feature(lat, lng)
        if props:
            clave = _pick(props, "cuenta", "cuenta_catastral", "clave_catastral", "cve_cat")
            uso = _pick(props, "uso_suelo", "uso_de_suelo", "uso")
            valor = _pick(props, "valor_catastral", "valor_catastral_mxn")
            colonia = _pick(props, "colonia", "nom_col")
            return {
                "clave_catastral": str(clave or f"CDMX-{digest}"),
                "uso_de_suelo": str(uso or "habitacional"),
                "valor_catastral_mxn": int(float(valor)) if valor else None,
                "valor_avaluo_mxn": None,
                "impuesto_predial_anual_mxn": None,
                "ultimo_pago_anio": None,
                "al_corriente": None,
                "colonia": colonia,
                "geocode": geo,
                "verification_token": f"CAT-LIVE-{digest}",
                "source": "live",
                "provider": "sig_cdmx_wfs",
            }

        return {
            "clave_catastral": None,
            "uso_de_suelo": None,
            "valor_catastral_mxn": None,
            "valor_avaluo_mxn": None,
            "impuesto_predial_anual_mxn": None,
            "ultimo_pago_anio": None,
            "al_corriente": None,
            "geocode": geo,
            "note": (
                "Geocodificado en CDMX; catastro WFS no respondió. "
                "Consulta manual en SIG CDMX con la dirección."
            ),
            "portal_url": "https://sig.cdmx.gob.mx/sig_cdmx/",
            "verification_token": f"CAT-LIVE-GEO-{digest}",
            "source": "live_partial",
            "provider": "nominatim",
        }

    return {
        "clave_catastral": None,
        "uso_de_suelo": None,
        "valor_catastral_mxn": None,
        "valor_avaluo_mxn": None,
        "impuesto_predial_anual_mxn": None,
        "ultimo_pago_anio": None,
        "al_corriente": None,
        "geocode": geo,
        "note": (
            f"Catastro {state}: geocodificado. Integración estatal pendiente; "
            "usa el portal catastral local con la dirección."
        ),
        "verification_token": f"CAT-LIVE-GEO-{digest}",
        "source": "live_partial",
        "provider": "nominatim",
    }
