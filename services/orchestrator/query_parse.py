"""
Mock SLM filter extraction — ported from casa-orquesta-mvp.

Runs before every orchestrator turn so locator_agent search_listings
receives state/city/type/beds/price from natural language (voice or chat).
"""
from __future__ import annotations

import os
import re
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from money_speech import extract_price_max_mxn, infer_listing_mode, normalize_money_in_text  # noqa: E402
from location_filters import extract_location_filters  # noqa: E402


def extract_filters(message: str) -> dict:
    """Extract structured search filters from es-MX user text."""
    normalized = normalize_money_in_text(message)
    msg = normalized.lower()
    f: dict = {}

    listing_mode = infer_listing_mode(message)
    if listing_mode:
        f["listing_mode"] = listing_mode

    price_max = extract_price_max_mxn(message, listing_mode=listing_mode)
    if price_max:
        f["price_max_mxn"] = price_max

    loc = extract_location_filters(message)
    f.update(loc)

    m = re.search(r"(\d+)\s*(rec[aá]maras?|habitacion|cuartos?|dormitorios?)", msg)
    if m:
        f["beds_min"] = int(m.group(1))

    m = re.search(r"(\d+)\s*(?:ba[nñ]os?)\b", msg)
    if m:
        f["baths_min"] = int(m.group(1))

    # Multi-type search only when THIS message names more than one type
    # (e.g. "casa o departamento"). A single token → ``type``; never both keys.
    types_found: list[str] = []
    if any(
        w in msg
        for w in ("departamento", "depa", "depto", "apartamento", "apartamentos", "apartment", "apartments")
    ):
        types_found.append("departamento")
    if re.search(r"\bcasa\b", msg) or re.search(r"\bhouse\b", msg) or re.search(r"\bhome\b", msg):
        types_found.append("casa")
    if any(w in msg for w in ("condominio", "condo", "townhouse")):
        types_found.append("condominio")
    if len(types_found) > 1:
        f["types"] = list(dict.fromkeys(types_found))
    elif len(types_found) == 1:
        f["type"] = types_found[0]
    elif "loft" in msg:
        f["type"] = "loft"
    elif "estudio" in msg:
        f["type"] = "estudio"
    elif "penthouse" in msg:
        f["type"] = "penthouse"

    if "infonavit" in msg:
        f["mortgage"] = "INFONAVIT"
    elif "fovissste" in msg or "fovisste" in msg:
        f["mortgage"] = "FOVISSSTE"
    elif "cofinanciamiento" in msg or "cofinavit" in msg:
        f["mortgage"] = "cofinanciamiento"
    elif any(w in msg for w in ("crédito bancario", "credito bancario", "hipoteca bancaria")):
        f["mortgage"] = "bancario"
    elif "pago de contado" in msg or "solo contado" in msg:
        f["mortgage"] = "contado"

    feats: list[str] = []
    for kw, label in [
        ("alberca", "alberca"),
        ("jardin", "jardín"),
        ("jardín", "jardín"),
        ("pet friendly", "pet friendly"),
        ("mascotas", "pet friendly"),
        ("amueblado", "amueblado"),
        ("vista", "vista"),
        ("rooftop", "rooftop"),
        ("terraza", "terraza"),
        ("estacionamiento", "estacionamiento"),
        ("parking", "estacionamiento"),
        ("elevador", "elevador"),
        ("ascensor", "elevador"),
        ("seguridad", "seguridad"),
        ("vigilancia", "seguridad"),
    ]:
        if kw in msg:
            feats.append(label)
    if feats:
        f["features"] = list(dict.fromkeys(feats))

    return f