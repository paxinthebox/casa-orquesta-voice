"""Registry connector configuration (live vs mock)."""
from __future__ import annotations

import os

REGISTRY_MODE = os.getenv("REGISTRY_MODE", "auto").strip().lower()  # mock | live | auto

INEGI_API_TOKEN = os.getenv("INEGI_API_TOKEN", "").strip()
SAT_CACHE_DIR = os.getenv(
    "SAT_CACHE_DIR",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "cache", "sat")),
)
SAT_CACHE_TTL_HOURS = int(os.getenv("SAT_CACHE_TTL_HOURS", "24"))

CDMX_CATASTRO_WFS_URL = os.getenv(
    "CDMX_CATASTRO_WFS_URL",
    "https://catalogov2.sig.cdmx.gob.mx/geoserver/wfs",
)
CDMX_CATASTRO_LAYERS = [
    layer.strip()
    for layer in os.getenv(
        "CDMX_CATASTRO_LAYERS",
        "geonode:catastro_cdmx,geonode:catastro,geonode:predios",
    ).split(",")
    if layer.strip()
]
RPP_LOOKUP_URL = os.getenv("RPP_LOOKUP_URL", "").strip()
GEOCODE_URL = os.getenv("GEOCODE_URL", "https://nominatim.openstreetmap.org/search")
HTTP_TIMEOUT_S = float(os.getenv("REGISTRY_HTTP_TIMEOUT_S", "12"))

SAT_LISTA_69B_URLS = {
    "definitivos": os.getenv(
        "SAT_LISTA_69B_DEFINITIVOS_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/Definitivos.csv",
    ),
    "presuntos": os.getenv(
        "SAT_LISTA_69B_PRESUNTOS_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/Presuntos.csv",
    ),
    "desvirtuados": os.getenv(
        "SAT_LISTA_69B_DESVIRTUADOS_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/Desvirtuados.csv",
    ),
}
SAT_LISTA_69_URLS = {
    "no_localizados": os.getenv(
        "SAT_LISTA_69_NO_LOCALIZADOS_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/No%20localizados.csv",
    ),
    "firmes": os.getenv(
        "SAT_LISTA_69_FIRMES_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/Firmes.csv",
    ),
    "exigibles": os.getenv(
        "SAT_LISTA_69_EXIGIBLES_URL",
        "http://omawww.sat.gob.mx/cifras_sat/Documents/Exigibles.csv",
    ),
}


def use_live() -> bool:
    return REGISTRY_MODE in ("live", "auto")


def require_live_only() -> bool:
    return REGISTRY_MODE == "live"
