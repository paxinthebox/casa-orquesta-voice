"""Natural-language reply helpers — aligned with MVP simulated_chat tone."""
from __future__ import annotations

from typing import Any


def _price_phrase(price_mxn: Any, *, listing_mode: str = "sale") -> str:
    try:
        val = float(price_mxn)
    except (TypeError, ValueError):
        return ""
    if val <= 0:
        return ""
    if listing_mode == "rent":
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f} millones de pesos al mes".replace(".0 ", " ")
        if val >= 1_000:
            miles = int(val / 1_000)
            return f"{miles} mil pesos al mes"
        return f"{int(val):,} pesos al mes".replace(",", ".")
    val /= 1_000_000
    if val == int(val):
        return f"{int(val)} millones de pesos"
    whole = int(val)
    frac = round((val - whole) * 10)
    if frac:
        return f"{whole} punto {frac} millones de pesos"
    return f"{whole} millones de pesos"


def describe_criteria(filters: dict[str, Any]) -> str:
    """Human-readable summary of parsed search filters."""
    parts: list[str] = []
    multi = filters.get("types")
    if isinstance(multi, list) and len(multi) > 1:
        labels = []
        for prop in multi:
            if prop == "departamento":
                labels.append("departamentos")
            elif prop == "casa":
                labels.append("casas")
            elif prop == "condominio":
                labels.append("condominios")
            else:
                labels.append(str(prop))
        if labels:
            parts.append(" o ".join(labels))
    else:
        prop = filters.get("type")
        if prop == "departamento":
            parts.append("departamentos")
        elif prop == "casa":
            parts.append("casas")
        elif prop == "condominio":
            parts.append("condominios")
        elif prop:
            parts.append(str(prop))

    mode = filters.get("listing_mode")
    if mode == "rent":
        parts.append("en renta anual")
    elif mode == "sale":
        parts.append("en venta")

    city = filters.get("city")
    state = filters.get("state")
    municipality = filters.get("municipality")
    fracc = filters.get("fraccionamiento")
    colonia = filters.get("colonia") or filters.get("neighborhood")
    locality = filters.get("locality")

    place_parts: list[str] = []
    if fracc:
        place_parts.append(f"Fracc. {fracc}")
    elif colonia:
        place_parts.append(f"Col. {colonia}")
    elif locality:
        place_parts.append(locality)
    mun_label = municipality or city
    if mun_label and mun_label != colonia:
        place_parts.append(str(mun_label))
    if state:
        place_parts.append(str(state))
    if place_parts:
        parts.append("en " + ", ".join(place_parts))
    elif city and state:
        parts.append(f"en {city}, {state}")
    elif city:
        parts.append(f"en {city}")
    elif state:
        parts.append(f"en {state}")

    if filters.get("beds_min"):
        parts.append(f"con {filters['beds_min']}+ recámaras")
    if filters.get("price_max_mxn"):
        parts.append(
            f"hasta {_price_phrase(filters['price_max_mxn'], listing_mode=str(mode or 'sale'))}"
        )

    feats = filters.get("features") or []
    if feats:
        parts.append("con " + ", ".join(str(f) for f in feats[:3]))

    if not parts:
        return "propiedades en CDMX o Morelos"
    return " ".join(parts)


def _listing_blurb(row: dict[str, Any]) -> str:
    title = str(row.get("title") or "Propiedad").strip()
    zone = str(row.get("neighborhood") or row.get("city") or "").strip()
    price_txt = _price_phrase(
        row.get("price_mxn") or 0,
        listing_mode=str(row.get("listing_mode") or "sale"),
    )
    if zone and zone.lower() not in title.lower():
        base = f"{title} en {zone}"
    else:
        base = title
    return f"{base}, {price_txt}" if price_txt else base


def looks_like_listing_results(results: list[dict[str, Any]]) -> bool:
    """True when tool results are property rows, not people/partner rows."""
    if not results:
        return False
    return any(r.get("price_mxn") is not None or r.get("beds") is not None for r in results)


def infer_people_kind(results: list[dict[str, Any]]) -> str:
    if not results:
        return "person"
    sample = results[0]
    if sample.get("lead_score") is not None or sample.get("stage"):
        return "buyer"
    if sample.get("license"):
        return "broker"
    if sample.get("firm") and not sample.get("availability"):
        return "broker"
    if sample.get("availability") is not None or sample.get("languages"):
        return "collaborator"
    return "collaborator"


def _person_blurb(row: dict[str, Any], *, kind: str) -> str:
    name = str(row.get("name") or "Contacto").strip()
    zone = ", ".join(
        x for x in [
            ", ".join(row.get("neighborhoods") or []) if isinstance(row.get("neighborhoods"), list) else "",
            str(row.get("city") or ""),
            str(row.get("state") or ""),
        ] if x
    )
    if kind == "buyer":
        extra = _price_phrase(row.get("budget_mxn") or 0) or str(row.get("stage") or "")
    elif kind == "broker":
        extra = str(row.get("firm") or row.get("license") or "")
    else:
        extra = ", ".join((row.get("specialties") or [])[:2]) if isinstance(row.get("specialties"), list) else ""
    if zone and extra:
        return f"{name} ({zone}, {extra})"
    if zone:
        return f"{name} ({zone})"
    return name


def format_people_search_reply(
    *,
    kind: str,
    filters: dict[str, Any],
    results: list[dict[str, Any]],
    match_quality: str = "exact",
    relaxations: list[str] | None = None,
) -> str:
    zone = describe_criteria(filters)
    labels = {
        "buyer": "compradores potenciales",
        "collaborator": "agentes colaboradores",
        "broker": "brokers",
    }
    label = labels.get(kind, "contactos")
    relaxations = relaxations or []
    near_note = _describe_relaxations(relaxations) if match_quality == "near" else ""

    if not results:
        return f"No encontré {label} {zone}. ¿Probamos otra zona o especialidad?"

    blurbs = "; ".join(_person_blurb(r, kind=kind) for r in results[:3])
    if match_quality == "near":
        if len(results) == 1:
            return (
                f"No encontré {label} {zone} exactos, pero te dejé una opción cercana"
                f"{near_note}: {blurbs}."
            )
        return (
            f"No encontré {label} {zone} exactos, pero te dejé {len(results)} opciones "
            f"cercanas{near_note}. Te recomiendo: {blurbs}."
        )
    if len(results) == 1:
        return f"Encontré un {label[:-1] if label.endswith('s') else label} {zone}: {blurbs}."
    return f"Encontré {len(results)} {label} {zone}. Te recomiendo: {blurbs}."


def _describe_relaxations(relaxations: list[str]) -> str:
    parts: list[str] = []
    if "price_max_mxn" in relaxations:
        parts.append("un poco arriba de tu presupuesto")
    if "beds_min" in relaxations:
        parts.append("con menos recámaras de las que pediste")
    if "location" in relaxations:
        parts.append("en zonas cercanas")
    if "type" in relaxations:
        parts.append("otro tipo de propiedad en la misma zona")
    if "mortgage" in relaxations:
        parts.append("sin el filtro de crédito exacto")
    if not parts:
        return ""
    if len(parts) == 1:
        return f" ({parts[0]})"
    return f" ({', '.join(parts[:-1])} y {parts[-1]})"


def format_listing_search_reply(
    *,
    filters: dict[str, Any],
    results: list[dict[str, Any]],
    user_query: str = "",
    compact_for_cards: bool = False,
    match_quality: str = "exact",
    relaxations: list[str] | None = None,
) -> str:
    criteria = describe_criteria(filters)
    relaxations = relaxations or []
    if not results:
        return (
            f"No encontré {criteria} por ahora. "
            "¿Te late que busque en otra zona o con otro presupuesto?"
        )

    near_note = _describe_relaxations(relaxations) if match_quality == "near" else ""

    if compact_for_cards:
        if match_quality == "near":
            if len(results) == 1:
                return (
                    f"No encontré {criteria} exactos, pero te dejé una opción cercana{near_note}."
                )
            return (
                f"No encontré {criteria} exactos, pero te dejé {len(results)} opciones "
                f"cercanas{near_note}. Revisa las tarjetas abajo."
            )
        if len(results) == 1:
            return f"Listo, encontré una opción de {criteria}. Te la dejé en la tarjeta."
        return (
            f"Listo, encontré {len(results)} opciones de {criteria}. "
            "Revisa las tarjetas abajo."
        )

    top = results[:3]
    blurbs = "; ".join(_listing_blurb(r) for r in top)
    if match_quality == "near":
        if len(results) == 1:
            return (
                f"No encontré {criteria} exactos, pero esta opción se acerca{near_note}: {blurbs}."
            )
        return (
            f"No encontré {criteria} exactos, pero encontré {len(results)} opciones cercanas"
            f"{near_note}. Las que más se acercan: {blurbs}."
        )
    if len(results) == 1:
        return f"Listo, encontré una opción de {criteria}: {blurbs}."
    return (
        f"Listo, encontré {len(results)} opciones de {criteria}. "
        f"Las que más te convienen: {blurbs}."
    )


def format_supervisor_search_reply(
    *,
    filters: dict[str, Any],
    sub_output: str,
    data: dict[str, Any] | None,
    people_kind: str | None = None,
) -> str:
    results = list((data or {}).get("results") or [])
    if results and looks_like_listing_results(results):
        return format_listing_search_reply(
            filters=filters,
            results=results,
            match_quality=str((data or {}).get("match_quality") or "exact"),
            relaxations=(data or {}).get("relaxations") or [],
        )
    if results:
        kind = people_kind or infer_people_kind(results)
        return format_people_search_reply(
            kind=kind,
            filters=filters,
            results=results,
            match_quality=str((data or {}).get("match_quality") or "exact"),
            relaxations=(data or {}).get("relaxations") or [],
        )
    if sub_output and "No encontré" in sub_output:
        if any(w in sub_output.lower() for w in ("colaborador", "broker", "comprador")):
            kind = "collaborator"
            if "broker" in sub_output.lower():
                kind = "broker"
            elif "comprador" in sub_output.lower():
                kind = "buyer"
            return format_people_search_reply(kind=kind, filters=filters, results=[])
        return format_listing_search_reply(filters=filters, results=[])
    return sub_output or format_listing_search_reply(filters=filters, results=[])
