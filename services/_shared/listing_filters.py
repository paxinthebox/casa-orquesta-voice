"""Shared listing filter helpers — property type + sale/rent matching."""
from __future__ import annotations

import re
from typing import Any

from location_registry import fold

# Canonical type → synonyms matched in type field, title, or description.
TYPE_SYNONYMS: dict[str, frozenset[str]] = {
    "departamento": frozenset({
        "departamento", "departamentos", "depto", "depa", "apartment",
        "apartamento", "apartamentos", "estudio",
    }),
    "casa": frozenset({"casa", "casas", "house", "vivienda"}),
    "condominio": frozenset({
        "condominio", "condominios", "condo", "condos", "townhouse",
    }),
    "loft": frozenset({"loft", "lofts"}),
    "penthouse": frozenset({"penthouse", "pent house"}),
    "terreno": frozenset({"terreno", "terrenos", "lote", "land"}),
}

RESIDENTIAL_TYPES = frozenset({"departamento", "casa", "condominio", "loft", "penthouse", "estudio"})

SHORT_TERM_RENT_MARKERS = frozenset({
    "airbnb", "air bnb", "vacacional", "vacation", "por noche", "por día",
    "por dia", "noche", "temporal", "corta estancia", "short term",
    "short-term", "estancia corta",
})

# Residential sale prices below this are almost always mis-tagged monthly rents.
_SALE_PRICE_FLOOR_MXN = 350_000


_LIVE_SOURCES = frozenset({
    "inmuebles24",
    "vivanuncios",
    "propiedades",
    "mercadolibre",
    "easybroker",
    "easybroker_mls",
    "lamudi",
})
_DEMO_CATALOG_ID = ("L-CDMX-", "L-MOR-")


def is_live_listing(row: dict[str, Any]) -> bool:
    source = str(row.get("source") or "")
    lid = str(row.get("id") or "")
    return source in _LIVE_SOURCES or lid.startswith(
        ("I24-", "VA-", "PROP-", "LAM-", "ML-", "EB-", "EB-MLS-"),
    )


def is_demo_catalog_listing(row: dict[str, Any]) -> bool:
    """Stage-pilot seed rows from data/seeds/listings.json (not portal inventory)."""
    lid = str(row.get("id") or "")
    if any(lid.startswith(prefix) for prefix in _DEMO_CATALOG_ID):
        return True
    source = str(row.get("source") or "")
    return source in {"catalog_demo", "mock"}


def _url_listing_mode(source_url: str) -> str | None:
    """Prefer portal path/slug tokens over ambiguous titles like 'Renta o Venta'."""
    url = str(source_url or "").lower()
    if not url:
        return None
    # Vivanuncios SERP prefix is authoritative: /a-venta-… vs /a-renta-…
    if re.search(r"/a-venta(?:-|/|$)", url):
        return "sale"
    if re.search(r"/a-renta(?:-|/|$)", url):
        return "rent"
    # Other portals: hyphen or slash bounded tokens (I24 …-renta-casa-…)
    rent_path = bool(re.search(r"(?:^|[/_-])renta(?:[/_-]|$)", url))
    sale_path = bool(re.search(r"(?:^|[/_-])venta(?:[/_-]|$)", url))
    if rent_path and not sale_path:
        return "rent"
    if sale_path and not rent_path:
        return "sale"
    return None


def listing_mode(row: dict[str, Any]) -> str:
    url_mode = _url_listing_mode(str(row.get("source_url") or ""))
    if url_mode:
        mode = url_mode
    else:
        blob = " ".join(
            str(row.get(k) or "")
            for k in ("title", "description", "source_url", "address")
        ).lower()
        rent_markers = ("en renta", " renta", "alquiler", "arrendar", "arrendamiento", "/renta-")
        sale_markers = ("en venta", " venta", "for sale", "/venta-")
        inferred_rent = any(m in blob for m in rent_markers) or bool(
            re.search(r"(?:^|\b)renta(?:\b|$)", blob)
        )
        inferred_sale = any(m in blob for m in sale_markers) or bool(
            re.search(r"(?:^|\b)venta(?:\b|$)", blob)
        )
        if inferred_rent and not inferred_sale:
            mode = "rent"
        elif inferred_sale and not inferred_rent:
            mode = "sale"
        else:
            mode = str(row.get("listing_mode") or "").strip().lower()
            if mode not in {"sale", "rent"}:
                mode = "sale"

    # Portal rows often tag monthly rents as "sale". CDMX/Morelos homes
    # under this floor are almost never real sale prices (except terrenos).
    price = row.get("price_mxn")
    prop = str(row.get("type") or row.get("property_type") or "").strip().lower()
    if (
        mode == "sale"
        and isinstance(price, (int, float))
        and 0 < int(price) < _SALE_PRICE_FLOOR_MXN
        and prop not in {"terreno", "lote", "land"}
    ):
        return "rent"
    return mode


def is_long_term_rent(row: dict[str, Any]) -> bool:
    if listing_mode(row) != "rent":
        return False
    term = str(row.get("rent_term") or "long").strip().lower()
    if term in {"short", "vacation", "vacacional"}:
        return False
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("title", "description", "features")
    ).lower()
    return not any(marker in blob for marker in SHORT_TERM_RENT_MARKERS)


def _canonical_type(want: str) -> str:
    key = want.strip().lower()
    for canonical, synonyms in TYPE_SYNONYMS.items():
        if key == canonical or key in synonyms:
            return canonical
    return key


def _type_from_url(source_url: str) -> str | None:
    """Portal slugs often name the true type when titles are wrong."""
    blob = str(source_url or "").lower().replace("-", " ")
    has_dep = any(t in blob for t in ("departamento", "departamentos", "depto", "depa"))
    has_casa = "casa" in blob or "casas" in blob
    if has_dep and not has_casa:
        return "departamento"
    if has_casa and not has_dep:
        return "casa"
    return None


def _description_lead_type(description: str) -> str | None:
    """Opening sentence from Inmuebles24 copy when it contradicts a wrong title."""
    lead = str(description or "").strip().lower()[:120]
    if lead.startswith(("departamento en venta", "departamento en renta", "este departamento")):
        return "departamento"
    if lead.startswith(("casa en venta", "casa en renta", "esta casa")):
        return "casa"
    return None


def infer_canonical_type(
    *,
    title: str = "",
    description: str = "",
    fallback: str = "",
    source_url: str = "",
) -> str:
    """
    Best-effort property type from title/description (beats portal ``type`` field).

    Title is authoritative when it names exactly one type; description fills gaps
    only when the title is silent. URL slugs and description leads override
  mis-titled portal rows (e.g. ``Casa en …`` with a departamento slug).
    """
    url_type = _type_from_url(source_url)
    lead_type = _description_lead_type(description)
    title_blob = str(title or "").lower()
    title_detected = _types_in_blob(title_blob)
    if len(title_detected) == 1:
        only = next(iter(title_detected))
        if only == "casa" and (url_type == "departamento" or lead_type == "departamento"):
            return "departamento"
        if only == "departamento" and (url_type == "casa" or lead_type == "casa"):
            return "casa"
        return only
    if len(title_detected) > 1:
        if "condominio" in title_detected and "condominio" in title_blob:
            return "condominio"
        if "departamento" in title_detected and "casa" not in title_detected:
            return "departamento"
        if "casa" in title_detected:
            return "casa"
        return sorted(title_detected)[0]

    desc_blob = str(description or "").lower()
    desc_detected = _types_in_blob(desc_blob) if desc_blob else set()
    if len(desc_detected) == 1:
        return next(iter(desc_detected))

    blob = f"{title} {description}".lower()
    detected = _types_in_blob(blob)
    if len(detected) == 1:
        return next(iter(detected))
    if len(detected) > 1:
        if "condominio" in detected and "condominio" in blob:
            return "condominio"
        if "departamento" in detected and "casa" not in detected:
            return "departamento"
        if "casa" in detected:
            return "casa"
        return sorted(detected)[0]
    fb = _canonical_type(fallback) if fallback else "inmueble"
    return fb if fb else "inmueble"


def _rebuild_listing_title_place(
    canonical_type: str,
    neighborhood: str,
    city: str = "",
) -> str:
    label = {
        "departamento": "Departamento",
        "casa": "Casa",
        "condominio": "Condominio",
        "loft": "Loft",
        "penthouse": "Penthouse",
        "terreno": "Terreno",
    }.get(canonical_type, "Propiedad")
    place = neighborhood
    if city and fold(neighborhood) != fold(city):
        place = f"{neighborhood}, {city}"
    return f"{label} en {place}"


def enrich_listing_location(row: dict[str, Any]) -> dict[str, Any]:
    """
    When portal ``neighborhood`` is wrong but the description names a registered
    fraccionamiento (e.g. Santa Fe Lifestyle vs Club de Golf Santa Fe), align
    neighborhood + title to the description-backed place.
    """
    from location_registry import resolve_fraccionamiento_from_text

    out = dict(row)
    desc = str(out.get("description") or "").strip()
    if not desc:
        return out
    hit = resolve_fraccionamiento_from_text(desc)
    if not hit:
        return out
    name, mun, st = hit
    city = str(out.get("city") or "").strip()
    if city and fold(mun) != fold(city):
        return out
    nb = str(out.get("neighborhood") or "").strip()
    if fold(name) == fold(nb):
        return out
    out["fraccionamiento"] = name
    out["neighborhood"] = name
    if mun:
        out["city"] = mun
    if st:
        out["state"] = st
    canon = str(out.get("type") or "").strip()
    if not canon or canon == "inmueble":
        canon = infer_canonical_type(
            title=str(row.get("title") or ""),
            description=desc,
            fallback=str(row.get("type") or ""),
            source_url=str(row.get("source_url") or row.get("url") or ""),
        )
    if canon and canon != "inmueble":
        out["title"] = _rebuild_listing_title_place(canon, name, str(out.get("city") or mun))
    return out


def align_listing_headline(
    title: str,
    canonical_type: str,
    *,
    neighborhood: str = "",
    city: str = "",
) -> str:
    """Rewrite misleading portal titles (e.g. ``Casa en…`` when type is departamento)."""
    if not title or not canonical_type:
        return title
    label = {
        "departamento": "Departamento",
        "casa": "Casa",
        "condominio": "Condominio",
        "loft": "Loft",
        "penthouse": "Penthouse",
        "terreno": "Terreno",
    }.get(canonical_type)
    if not label:
        return title.strip()
    t = title.strip()
    lower = t.lower()
    wrong_casa = canonical_type == "departamento" and lower.startswith("casa")
    wrong_dep = canonical_type == "casa" and lower.startswith("departamento")
    if not wrong_casa and not wrong_dep:
        return t
    rest = re.sub(
        r"^\s*(?:casa|departamento|depto|depa)\s+en\s+",
        "",
        t,
        flags=re.I,
    ).strip()
    if rest:
        return f"{label} en {rest}"
    place = str(neighborhood or city or "").strip()
    if place:
        suffix = f", {city}" if neighborhood and city and neighborhood != city else ""
        return f"{label} en {place}{suffix}"
    return label


def canonicalize_listing_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with ``type``, ``title``, and ``listing_mode`` aligned to portal truth."""
    out = enrich_listing_location(dict(row))
    canonical = infer_canonical_type(
        title=str(out.get("title") or row.get("title") or ""),
        description=str(out.get("description") or row.get("description") or ""),
        fallback=str(row.get("type") or ""),
        source_url=str(row.get("source_url") or row.get("url") or ""),
    )
    if canonical and canonical != "inmueble":
        out["type"] = canonical
        out["title"] = align_listing_headline(
            str(out.get("title") or row.get("title") or ""),
            canonical,
            neighborhood=str(out.get("neighborhood") or ""),
            city=str(out.get("city") or ""),
        )
    if is_live_listing(out) or str(out.get("source") or "") == "inmuebles24":
        out["listing_mode"] = listing_mode(out)
    return out


def normalize_type_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """
    Canonical type keys for matching: ``type`` OR ``types``, never both.

    Maps ``property_types`` from the mobile formulary when no type is set.
    When a single ``type`` is present, drops stale multi-type ``types[]``
    left over from a prior turn (``types`` wins in apply_types_filter).
    """
    out = dict(filters)
    if not out.get("types") and not out.get("type"):
        prop_types = out.get("property_types")
        if isinstance(prop_types, list) and prop_types:
            cleaned = [str(t).strip() for t in prop_types if str(t).strip()]
            if len(cleaned) == 1:
                out["type"] = cleaned[0]
            elif cleaned:
                out["types"] = cleaned
    if out.get("type") and out.get("types"):
        out.pop("types", None)
    if out.get("type") or out.get("types"):
        out.pop("property_types", None)
    return out


_CASA_RE = re.compile(r"\bcasa\b", re.I)


def _listing_type_blob(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(k) or "")
        for k in ("title", "description", "type")
    ).lower()


def _types_in_blob(blob: str) -> set[str]:
    """Types named in title/description (more reliable than portal type field)."""
    found: set[str] = set()
    if any(s in blob for s in TYPE_SYNONYMS.get("departamento", frozenset())):
        found.add("departamento")
    if _CASA_RE.search(blob):
        found.add("casa")
    if any(s in blob for s in TYPE_SYNONYMS.get("condominio", frozenset())):
        found.add("condominio")
    if "loft" in blob:
        found.add("loft")
    if "penthouse" in blob:
        found.add("penthouse")
    if any(s in blob for s in TYPE_SYNONYMS.get("terreno", frozenset())):
        found.add("terreno")
    return found


def listing_matches_type(row: dict[str, Any], want_type: str) -> bool:
    want = _canonical_type(want_type)
    if not want or want == "inmueble":
        return True

    canonical = infer_canonical_type(
        title=str(row.get("title") or ""),
        description=str(row.get("description") or ""),
        fallback=str(row.get("type") or ""),
        source_url=str(row.get("source_url") or row.get("url") or ""),
    )
    if canonical and canonical != "inmueble":
        return canonical == want

    copy_blob = " ".join(
        str(row.get(k) or "")
        for k in ("title", "description")
    ).lower()
    title_blob = str(row.get("title") or "").lower()
    title_detected = _types_in_blob(title_blob)
    if title_detected:
        if want in title_detected:
            return True
        if want == "condominio" and "condominio" in title_blob:
            return True
        return False

    detected = _types_in_blob(copy_blob)

    # Title/description beat mis-tagged portal ``type`` fields.
    if detected:
        if want in detected:
            return True
        if want == "condominio" and "condominio" in copy_blob:
            return True
        return False

    row_type = str(row.get("type") or "").strip().lower()
    if row_type == want:
        return True
    if row_type in TYPE_SYNONYMS.get(want, frozenset()):
        return True

    want_syns = TYPE_SYNONYMS.get(want, frozenset({want}))
    if row_type in want_syns:
        return True

    blob = _listing_type_blob(row)
    if any(s in blob for s in want_syns):
        return True

    return False


def apply_listing_mode_filter(
    rows: list[dict[str, Any]],
    want_mode: str | None,
) -> list[dict[str, Any]]:
    if not want_mode:
        return rows
    mode = want_mode.strip().lower()
    if mode == "rent":
        return [r for r in rows if listing_mode(r) == "rent" and is_long_term_rent(r)]
    if mode == "sale":
        return [r for r in rows if listing_mode(r) == "sale"]
    return rows


def apply_type_filter(rows: list[dict[str, Any]], want_type: str) -> list[dict[str, Any]]:
    want = str(want_type or "").strip()
    if not want:
        return rows
    return [r for r in rows if listing_matches_type(r, want)]


def apply_types_filter(rows: list[dict[str, Any]], want_types: list[str]) -> list[dict[str, Any]]:
    cleaned = [str(t).strip() for t in want_types if str(t).strip()]
    if not cleaned:
        return rows
    if len(cleaned) == 1:
        return apply_type_filter(rows, cleaned[0])
    return [r for r in rows if any(listing_matches_type(r, t) for t in cleaned)]
