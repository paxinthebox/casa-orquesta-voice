"""Parse Lamudi listing titles — inventory export has weak city/suburb fields."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))

# "Casa en Venta en Prado Churubusco Prado Churubusco, Coyoacán, Ciudad de México …"
_TITLE_TRIPLE_RE = re.compile(
    r"en (?:Venta|Renta) en\s+(.+?),\s*([^,]+),\s*([^,]+?)(?:\s{2,}|\s+[A-ZÁÉÍÓÚÑ].{20,}|$)",
    re.IGNORECASE,
)

_CDMX_MARKERS = frozenset({"ciudad de mexico", "cdmx", "df", "distrito federal"})
_MORELOS_CITIES = frozenset({
    "cuernavaca", "jiutepec", "temixco", "cuautla", "emiliano zapata",
    "xochitepec", "yautepec", "tepoztlan", "jojutla",
})


def fix_mojibake(text: str) -> str:
    """Repair common Lamudi inventory UTF-8 shown as Latin-1."""
    if not text or "Ã" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_place(text: str) -> str:
    cleaned = fix_mojibake(text).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Drop duplicated neighborhood token ("Prado X Prado X" → "Prado X")
    parts = cleaned.split()
    if len(parts) >= 4 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if " ".join(parts[:half]).casefold() == " ".join(parts[half:]).casefold():
            cleaned = " ".join(parts[:half])
    return cleaned


def _resolve_state(raw_state: str, city: str) -> str:
    st = raw_state.strip()
    folded = _fold(st)
    city_fold = _fold(city)
    if folded in _CDMX_MARKERS or "ciudad de mexico" in folded:
        return "CDMX"
    if folded == "morelos" or city_fold in _MORELOS_CITIES:
        return "Morelos"
    if folded in {"cdmx", "df"}:
        return "CDMX"
    return st


def parse_lamudi_title_location(title: str) -> tuple[str, str, str]:
    """Return neighborhood, city, state from a Lamudi MX title line."""
    title = fix_mojibake(title)
    match = _TITLE_TRIPLE_RE.search(title)
    if not match:
        return "", "", ""
    neighborhood = _clean_place(match.group(1))
    city = _clean_place(match.group(2))
    state = _resolve_state(match.group(3), city)
    return neighborhood, city, state


def enrich_lamudi_location(row: dict[str, Any]) -> dict[str, Any]:
    """Fill neighborhood/city/state from title when inventory geo fields are junk."""
    out = dict(row)
    title = str(out.get("title") or "")
    nbhd, city, state = parse_lamudi_title_location(title)
    if nbhd:
        out["neighborhood"] = nbhd
    if city:
        out["city"] = city
    if state:
        out["state"] = state
    desc = str(out.get("description") or "")
    if desc and not out.get("neighborhood"):
        nbhd2, city2, state2 = parse_lamudi_title_location(desc[:240])
        out.setdefault("neighborhood", nbhd2)
        out.setdefault("city", city2)
        out.setdefault("state", state2)
    return out
