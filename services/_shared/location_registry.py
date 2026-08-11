"""
Mexican location hierarchy for CDMX + Morelos pilot search.

Levels (most → least specific):
  fraccionamiento → colonia → localidad → municipio/alcaldía → estado

Listings and people rows use ``city`` for municipio/alcaldía and ``neighborhood``
for colonia or fraccionamiento; this module resolves user text into the full chain.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --- Pilot geography (CDMX alcaldías + Morelos municipios) -------------------

CDMX_MUNICIPALITIES: tuple[str, ...] = (
    "Álvaro Obregón",
    "Azcapotzalco",
    "Benito Juárez",
    "Coyoacán",
    "Cuajimalpa",
    "Cuauhtémoc",
    "Gustavo A. Madero",
    "Iztacalco",
    "Iztapalapa",
    "La Magdalena Contreras",
    "Miguel Hidalgo",
    "Milpa Alta",
    "Tláhuac",
    "Tlalpan",
    "Venustiano Carranza",
    "Xochimilco",
)

MORELOS_MUNICIPALITIES: tuple[str, ...] = (
    "Cuernavaca",
    "Jiutepec",
    "Temixco",
    "Cuautla",
    "Emiliano Zapata",
    "Xochitepec",
    "Yautepec",
    "Tepoztlán",
    "Jojutla",
    "Huitzilac",
    "Tlaquiltenango",
    "Mazatepec",
    "Puente de Ixtla",
    "Ayala",
    "Atlatlahucan",
    "Zacatepec",
    "Jantetelco",
    "Tlayacapan",
    "Totolapan",
    "Tetecala",
    "Miacatlán",
    "Coatlán del Río",
    "Tetela del Volcán",
)

# (alias, canonical colonia, municipio, state)
CDMX_COLONIAS: tuple[tuple[str, str, str], ...] = (
    ("polanco", "Polanco", "Miguel Hidalgo"),
    ("granada", "Granada", "Miguel Hidalgo"),
    ("ampliacion granada", "Ampliación Granada", "Miguel Hidalgo"),
    ("lomas de chapultepec", "Lomas de Chapultepec", "Miguel Hidalgo"),
    ("lomas de bezares", "Lomas de Bezares", "Miguel Hidalgo"),
    ("condesa", "Condesa", "Cuauhtémoc"),
    ("roma norte", "Roma Norte", "Cuauhtémoc"),
    ("roma", "Roma Norte", "Cuauhtémoc"),
    ("juarez", "Juárez", "Cuauhtémoc"),
    ("juárez", "Juárez", "Cuauhtémoc"),
    ("del valle", "Del Valle", "Benito Juárez"),
    ("del valle norte", "Del Valle Norte", "Benito Juárez"),
    ("narvarte", "Narvarte", "Benito Juárez"),
    ("narvarte poniente", "Narvarte Poniente", "Benito Juárez"),
    ("santa fe", "Santa Fe", "Álvaro Obregón"),
    ("coyoacan", "Coyoacán", "Coyoacán"),
    ("coyoacán", "Coyoacán", "Coyoacán"),
    ("san angel", "San Ángel", "Álvaro Obregón"),
    ("san ángel", "San Ángel", "Álvaro Obregón"),
    ("jardines del pedregal", "Jardines del Pedregal", "Álvaro Obregón"),
    ("tabacalera", "Tabacalera", "Cuauhtémoc"),
    ("san rafael", "San Rafael", "Cuauhtémoc"),
    ("prados churubusco", "Prados Churubusco", "Coyoacán"),
    ("prados de churubusco", "Prados Churubusco", "Coyoacán"),
    ("prados churubisco", "Prados Churubusco", "Coyoacán"),
)

# (alias, canonical name, municipio, state)
FRACCIONAMIENTOS: tuple[tuple[str, str, str, str], ...] = (
    ("club de golf santa fe", "Club de Golf Santa Fe", "Álvaro Obregón", "CDMX"),
    ("club de golf santafe", "Club de Golf Santa Fe", "Álvaro Obregón", "CDMX"),
    ("tres cumbres santa fe", "Tres Cumbres Santa Fe", "Álvaro Obregón", "CDMX"),
    ("burgos bugambilias", "Burgos Bugambilias", "Temixco", "Morelos"),
    ("fraccionamiento burgos bugambilias", "Burgos Bugambilias", "Temixco", "Morelos"),
    ("lomas de cortes", "Lomas de Cortés", "Cuernavaca", "Morelos"),
    ("lomas de cortés", "Lomas de Cortés", "Cuernavaca", "Morelos"),
    ("vista hermosa", "Vista Hermosa", "Cuernavaca", "Morelos"),
    ("rancho cortes", "Rancho Cortés", "Cuernavaca", "Morelos"),
    ("rancho cortés", "Rancho Cortés", "Cuernavaca", "Morelos"),
    ("club de golf", "Club de Golf", "Cuernavaca", "Morelos"),
    ("los laureles", "Los Laureles", "Xochitepec", "Morelos"),
    ("villa verdun", "Villa Verdún", "Cuernavaca", "Morelos"),
    ("villa verdún", "Villa Verdún", "Cuernavaca", "Morelos"),
    ("tamoanchan", "Tamoanchan", "Jiutepec", "Morelos"),
    ("santa fe lifestyle", "Santa Fe Lifestyle", "Xochitepec", "Morelos"),
    ("fraccionamiento santa fe lifestyle", "Santa Fe Lifestyle", "Xochitepec", "Morelos"),
)

MORELOS_LOCALITIES: tuple[tuple[str, str, str], ...] = (
    ("pueblo chiconcuac", "Pueblo Chiconcuac", "Xochitepec"),
    ("oaxtepec", "Oaxtepec", "Yautepec"),
)

# Directional zones — broader than a single fraccionamiento (alias → canonical key).
LOCATION_ZONE_ALIASES: tuple[tuple[str, str], ...] = (
    ("al norte de cuernavaca", "norte de cuernavaca"),
    ("norte de cuernavaca", "norte de cuernavaca"),
    ("norte cuernavaca", "norte de cuernavaca"),
)

# (zone_key, municipio, state, match tokens in listing location blob)
LOCATION_ZONES: dict[str, tuple[str, str, frozenset[str]]] = {
    "norte de cuernavaca": (
        "Cuernavaca",
        "Morelos",
        frozenset({
            "norte de cuernavaca",
            "norte cuernavaca",
            "zona norte",
            "lomas de cortes",
            "lomas de cortés",
            "lomas de cuernavaca",
            "vista hermosa",
            "rancho cortes",
            "rancho cortés",
            "lomas tetela",
            "real de tetela",
            "los limoneros",
            "loma linda",
            "paloma de la paz",
            "cantarranas",
            "delicias",
            "tres marias",
            "tres marías",
            "huitzilac",
            "ahuatlan",
            "ahuatlán",
            "kloster",
            "monte casino",
            "zacapexco",
        }),
    ),
}

_STATE_ALIASES: dict[str, str] = {
    "cdmx": "CDMX",
    "ciudad de mexico": "CDMX",
    "ciudad de méxico": "CDMX",
    "df": "CDMX",
    "distrito federal": "CDMX",
    "morelos": "Morelos",
    "mor": "Morelos",
    "mor.": "Morelos",
}

_FRACC_RE = re.compile(
    r"(?i)(?:fracc(?:ionamiento)?|fracc\.)\s+"
    r"([\wÁÉÍÓÚáéíóúñ0-9][\w\sÁÉÍÓÚáéíóúñ\-\.]{2,45}?)"
    r"(?:\s*,|\s+en\s+|\s*$|[\.!,;])"
)
_COLONIA_RE = re.compile(
    r"(?i)(?:col(?:onia)?|col\.)\s+"
    r"([\wÁÉÍÓÚáéíóúñ0-9][\w\sÁÉÍÓÚáéíóúñ\-\.]{2,45}?)"
    r"(?:\s*,|\s+en\s+|\s*$|[\.!,;])"
)
_MUNICIPIO_RE = re.compile(
    r"(?i)(?:municipio|alcald[ií]a)\s+"
    r"([\wÁÉÍÓÚáéíóúñ][\w\sÁÉÍÓÚáéíóúñ\-\.]{2,45}?)"
    r"(?:\s*,|\s+en\s+|\s*$|[\.!,;])"
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _normalize_text(message: str) -> str:
    msg = fold(message)
    return msg.replace("santafe", "santa fe").replace("santa fé", "santa fe")


@dataclass
class ResolvedLocation:
    state: str = ""
    municipality: str = ""
    city: str = ""
    locality: str = ""
    colonia: str = ""
    fraccionamiento: str = ""
    zone: str = ""
    level: str = ""
    states: list[str] = field(default_factory=list)

    def to_filters(self) -> dict[str, Any]:
        """Map to session/search filter keys (backward compatible)."""
        out: dict[str, Any] = {}
        if self.states:
            out["states"] = list(self.states)
        if self.state:
            out["state"] = self.state
        if self.municipality:
            out["municipality"] = self.municipality
        if self.city:
            out["city"] = self.city
        if self.locality:
            out["locality"] = self.locality
        if self.colonia:
            out["colonia"] = self.colonia
        if self.fraccionamiento:
            out["fraccionamiento"] = self.fraccionamiento
        # Legacy ``neighborhood`` = most specific colonia or fraccionamiento.
        hood = self.fraccionamiento or self.colonia or self.locality
        if hood:
            out["neighborhood"] = hood
        if self.zone:
            out["zone"] = self.zone
        if self.level:
            out["location_level"] = self.level
        return out


def _canonical_city(state: str, municipality: str) -> str:
    if state == "CDMX" and municipality:
        return "Ciudad de México"
    return municipality


def _match_longest_keyword(msg: str, keywords: list[tuple[str, ...]]) -> tuple[str, ...] | None:
    for row in sorted(keywords, key=lambda r: -len(r[0])):
        if row[0] in msg:
            return row
    return None


def resolve_fraccionamiento_from_text(text: str) -> tuple[str, str, str] | None:
    """
    Return ``(canonical name, municipio, state)`` when *text* names a registered
    fraccionamiento (description copy is often more accurate than portal fields).
    """
    if not (text or "").strip():
        return None
    msg = _normalize_text(text)
    fracc_row = _match_longest_keyword(
        msg,
        [(fold(a), name, mun, st) for a, name, mun, st in FRACCIONAMIENTOS],
    )
    if fracc_row:
        _, name, mun, st = fracc_row
        return name, mun, st
    return None


def _parse_state_tokens(msg: str) -> tuple[str, list[str]]:
    has_cdmx = any(t in msg for t in _STATE_ALIASES if _STATE_ALIASES[t] == "CDMX")
    has_morelos = "morelos" in msg or " mor." in msg or msg.endswith(" mor")
    if has_cdmx and has_morelos:
        return "", ["CDMX", "Morelos"]
    if has_cdmx:
        return "CDMX", []
    if has_morelos:
        return "Morelos", []
    return "", []


def _municipality_entries() -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for name in CDMX_MUNICIPALITIES:
        rows.append((fold(name), name, "CDMX"))
        for part in name.split():
            if len(part) > 3:
                rows.append((fold(part), name, "CDMX"))
    for name in MORELOS_MUNICIPALITIES:
        rows.append((fold(name), name, "Morelos"))
    return rows


def _match_zone(msg: str) -> str:
    """Return canonical zone key when message names a directional area."""
    for alias, key in sorted(LOCATION_ZONE_ALIASES, key=lambda r: -len(r[0])):
        if alias in msg:
            return key
    return ""


def resolve_location(message: str) -> ResolvedLocation:
    """Resolve user text to estado → municipio → colonia/fraccionamiento chain."""
    msg = _normalize_text(message)
    resolved = ResolvedLocation()
    state, states = _parse_state_tokens(msg)
    resolved.state = state
    resolved.states = states

    zone_key = _match_zone(msg)
    if zone_key:
        mun, st, _tokens = LOCATION_ZONES[zone_key]
        resolved.zone = zone_key
        resolved.municipality = mun
        resolved.state = st or resolved.state
        resolved.city = _canonical_city(resolved.state, mun)
        resolved.level = "zone"
        return resolved

    # --- Fraccionamiento (registry + labeled) --------------------------------
    fracc_row = _match_longest_keyword(
        msg,
        [(fold(a), name, mun, st) for a, name, mun, st in FRACCIONAMIENTOS],
    )
    if fracc_row:
        _, name, mun, st = fracc_row
        resolved.fraccionamiento = name
        resolved.municipality = mun
        resolved.state = st
        resolved.city = _canonical_city(st, mun)
        resolved.level = "fraccionamiento"
        return resolved

    fracc_m = _FRACC_RE.search(message)
    if fracc_m:
        name = fracc_m.group(1).strip(" .,")
        resolved.fraccionamiento = name
        resolved.level = "fraccionamiento"
        # Parent municipio/state from remainder of message.
        tail = msg[fracc_m.end():]
        _apply_municipality_and_state(resolved, tail or msg)
        if resolved.municipality:
            resolved.city = _canonical_city(resolved.state, resolved.municipality)
        return resolved

    # --- Colonia (registry + labeled) ----------------------------------------
    col_row = _match_longest_keyword(
        msg,
        [(fold(a), name, mun) for a, name, mun in CDMX_COLONIAS],
    )
    if col_row:
        _, name, mun = col_row
        resolved.colonia = name
        resolved.municipality = mun
        resolved.state = "CDMX"
        resolved.city = "Ciudad de México"
        resolved.level = "colonia"
        return resolved

    col_m = _COLONIA_RE.search(message)
    if col_m:
        name = col_m.group(1).strip(" .,")
        resolved.colonia = name
        resolved.level = "colonia"
        tail = msg[col_m.end():]
        _apply_municipality_and_state(resolved, tail or msg)
        if resolved.municipality:
            resolved.city = _canonical_city(resolved.state, resolved.municipality)
        elif resolved.state == "CDMX":
            resolved.city = "Ciudad de México"
        return resolved

    # --- Localidad (pueblo / ejido within municipio) -------------------------
    loc_row = _match_longest_keyword(
        msg,
        [(fold(a), name, mun) for a, name, mun in MORELOS_LOCALITIES],
    )
    if loc_row:
        _, name, mun = loc_row
        resolved.locality = name
        resolved.municipality = mun
        resolved.state = "Morelos"
        resolved.city = mun
        resolved.level = "localidad"
        return resolved

    # --- Municipio / alcaldía (labeled or keyword) -----------------------------
    mun_m = _MUNICIPIO_RE.search(message)
    if mun_m:
        name = mun_m.group(1).strip(" .,")
        resolved.municipality = name
        resolved.level = "municipio"
        tail = msg[mun_m.end():]
        if not resolved.state:
            _apply_municipality_and_state(resolved, tail or msg)
        resolved.city = _canonical_city(resolved.state, resolved.municipality)
        return resolved

    mun_row = _match_longest_keyword(msg, _municipality_entries())
    if mun_row:
        _, name, st = mun_row
        resolved.municipality = name
        resolved.state = st or resolved.state
        resolved.city = _canonical_city(resolved.state, name)
        resolved.level = "municipio"
        return resolved

    # --- Estado only -----------------------------------------------------------
    if resolved.state or resolved.states:
        resolved.level = "estado"
    return resolved


def _apply_municipality_and_state(resolved: ResolvedLocation, msg: str) -> None:
    if not resolved.state:
        st, states = _parse_state_tokens(msg)
        resolved.state = st
        resolved.states = states
    if not resolved.municipality:
        mun_row = _match_longest_keyword(msg, _municipality_entries())
        if mun_row:
            _, name, st = mun_row
            resolved.municipality = name
            if not resolved.state:
                resolved.state = st


def zone_match_tokens(zone_key: str) -> frozenset[str]:
    """Location blob tokens for a registered directional zone."""
    row = LOCATION_ZONES.get(zone_key)
    if not row:
        return frozenset()
    return row[2]


def location_needles_from_filters(filters: dict[str, Any], *, query: str = "") -> set[str]:
    """Search needles from resolved hierarchy (most specific wins)."""
    needles: set[str] = set()
    text = _normalize_text(query)

    zone = str(filters.get("zone") or "").strip()
    if zone:
        # Zone filters use listing_matches_zone — no single-neighborhood needles.
        return needles

    fracc = str(filters.get("fraccionamiento") or "").strip()
    colonia = str(filters.get("colonia") or "").strip()
    locality = str(filters.get("locality") or "").strip()
    municipality = str(filters.get("municipality") or "").strip()
    city = str(filters.get("city") or "").strip()
    neighborhood = str(filters.get("neighborhood") or "").strip()

    if fracc:
        needles.add(fold(fracc))
        for alias, name, _mun, _st in FRACCIONAMIENTOS:
            if fold(name) == fold(fracc) or alias in fold(fracc):
                needles.add(alias)
        return needles

    if colonia or (neighborhood and not locality):
        name = colonia or neighborhood
        needles.add(fold(name))
        for alias, cname, _mun in CDMX_COLONIAS:
            if fold(cname) == fold(name) or alias in fold(name):
                needles.add(alias)
        return needles

    if locality:
        needles.add(fold(locality))
        if municipality:
            needles.add(fold(municipality))
        return needles

    if municipality:
        needles.add(fold(municipality))
        return needles

    if city and fold(city) != fold("Ciudad de México"):
        needles.add(fold(city))
        return needles

    # CDMX-wide
    if any(t in text for t in ("cdmx", "ciudad de mexico", "df")) or filters.get("state") == "CDMX":
        needles.add(fold("Ciudad de México"))
        needles.update({"cdmx", "ciudad de mexico", "df", "distrito federal"})

    return needles


def is_specific_locality(filters: dict[str, Any]) -> bool:
    """True when user named fraccionamiento or a registered colonia/frac."""
    if filters.get("zone"):
        return False
    if filters.get("fraccionamiento"):
        return True
    name = str(filters.get("colonia") or filters.get("neighborhood") or "")
    if not name:
        return False
    folded = fold(name)
    return any(
        fold(entry[1]) == folded or entry[0] in folded
        for entry in FRACCIONAMIENTOS
    ) or any(
        fold(entry[1]) == folded or entry[0] in folded
        for entry in CDMX_COLONIAS
    )


def load_places_extension(path: Path | None = None) -> None:
    """Optional JSON overlay: data/geo/pilot_places.json."""
    global FRACCIONAMIENTOS, CDMX_COLONIAS, MORELOS_LOCALITIES
    if path is None:
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidate = parent / "data" / "geo" / "pilot_places.json"
            if candidate.is_file():
                path = candidate
                break
    if not path or not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    fracs = list(FRACCIONAMIENTOS)
    colonias = list(CDMX_COLONIAS)
    locs = list(MORELOS_LOCALITIES)
    for entry in raw.get("fraccionamientos") or []:
        if isinstance(entry, dict):
            fracs.append((
                fold(str(entry.get("alias") or entry.get("name") or "")),
                str(entry.get("name") or ""),
                str(entry.get("municipality") or ""),
                str(entry.get("state") or "Morelos"),
            ))
    for entry in raw.get("colonias") or []:
        if isinstance(entry, dict):
            colonias.append((
                fold(str(entry.get("alias") or entry.get("name") or "")),
                str(entry.get("name") or ""),
                str(entry.get("municipality") or "Miguel Hidalgo"),
            ))
    for entry in raw.get("localidades") or []:
        if isinstance(entry, dict):
            locs.append((
                fold(str(entry.get("alias") or entry.get("name") or "")),
                str(entry.get("name") or ""),
                str(entry.get("municipality") or ""),
            ))
    FRACCIONAMIENTOS = tuple(fracs)
    CDMX_COLONIAS = tuple(colonias)
    MORELOS_LOCALITIES = tuple(locs)


load_places_extension()
