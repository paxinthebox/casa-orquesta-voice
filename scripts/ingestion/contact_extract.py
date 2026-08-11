"""Extract publisher/agent names from portal payloads and listing text."""
from __future__ import annotations

import re
from typing import Any

_PUBLISHER_KEYS = (
    "publisher_name",
    "publisherName",
    "publisher",
    "agency",
    "agencyName",
    "realEstate",
    "realEstateAgency",
    "inmobiliaria",
    "advertiser",
    "professional",
    "seller",
    "owner",
    "company",
    "office",
)

_AGENT_KEYS = (
    "agent_name",
    "agentName",
    "agent",
    "contact_name",
    "contactName",
    "contact",
    "postingContact",
    "sellerContact",
    "broker",
    "asesor",
)

_NAME_SUBKEYS = ("name", "fullName", "displayName", "legalName", "title", "label", "value")

_JUNK_NAME_RE = re.compile(
    r"^(none|null|undefined|n/a|na|sin\s+(?:nombre|datos)|publicador|anunciante)$",
    re.IGNORECASE,
)

_AGENCY_PATTERNS = (
    re.compile(
        r"(?i)\b((?:re\s*/?\s*max|remax)[\w\s\-]*inmobiliaria(?:\s+en\s+[\w\sÁÉÍÓÚáéíóúñ]+)?)"
    ),
    re.compile(
        r"(?i)\b(inmobiliaria\s+[\wÁÉÍÓÚáéíóúñ&\.\-]{2,45}?)"
        r"(?:\s+le\s+presenta|\s+presenta|\s+en\s+|\s*$|[\.!,])"
    ),
    re.compile(r"(?i)\b([\wÁÉÍÓÚáéíóúñ&\.\-]{2,35}\s+vende)\s+inmobiliaria"),
    re.compile(r"(?i)(?:con|de)\s+([\wÁÉÍÓÚáéíóúñ&\.\-]{2,40}\s+inmobiliaria)"),
    re.compile(
        r"(?i)\b(century\s*21[\w\s\-]{0,35}|coldwell\s+banker[\w\s\-]{0,35}|"
        r"keller\s+williams[\w\s\-]{0,35})"
    ),
    re.compile(
        r"(?i)\b((?!col(?:onia)?\b)[\wÁÉÍÓÚáéíóúñ&\.\-]{4,35}\s+inmobiliaria)\b"
    ),
)

_AGENCY_WORDS = frozenset({
    "inmobiliaria", "bienes raices", "bienes raíces", "real estate", "realty",
    "properties", "propiedades", "grupo", "consultores",
})

_AGENT_LINE_RE = re.compile(
    r"(?i)(?:asesor(?:a)?(?:\s+inmobiliario)?|agente(?:\s+inmobiliario)?|"
    r"contacto|att(?:ention)?\.?)\s*:?\s*"
    r"([\wÁÉÍÓÚáéíóúñ\. '\-]{3,45})"
)

_TITLE_AGENCY_RE = re.compile(
    r"(?i)(?:departamento|casa|propiedad|inmueble|loft|condominio)[^\.]{0,40}?"
    r"en\s+venta\s+en\s+([\wÁÉÍÓÚáéíóúñ&\.\-]{2,40})(?:\.|,|\s|$)"
)

_TRAILING_LOCATION_RE = re.compile(
    r"(?i)\s+en\s+(cuernavaca|cdmx|ciudad\s+de\s+m[eé]xico|morelos|jiutepec|"
    r"temixco|cuautla|m[eé]xico)\s*$"
)


def _pick_name(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("{") or text.startswith("["):
            return ""
        if _JUNK_NAME_RE.match(text):
            return ""
        return text
    if isinstance(value, dict):
        for key in _NAME_SUBKEYS:
            if key in value:
                picked = _pick_name(value[key])
                if picked:
                    return picked
        return ""
    if isinstance(value, list):
        for item in value:
            picked = _pick_name(item)
            if picked:
                return picked
    return ""


def _deep_find_name(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in raw:
            picked = _pick_name(raw[key])
            if picked:
                return picked

    stack: list[Any] = list(raw.values())
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        nid = id(node)
        if nid in seen:
            continue
        seen.add(nid)
        if isinstance(node, dict):
            for key in keys:
                if key in node:
                    picked = _pick_name(node[key])
                    if picked:
                        return picked
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return ""


def _clean_agency_name(name: str) -> str:
    text = re.sub(r"\s+", " ", name).strip(" .,;:!?")
    text = _TRAILING_LOCATION_RE.sub("", text).strip()
    if len(text) < 3:
        return ""
    lower = text.lower()
    if any(
        lower.startswith(prefix)
        for prefix in (
            "colonia ",
            "col ",
            "col.",
            "fracc",
            "fraccionamiento ",
            "departamento ",
            "casa ",
            "venta ",
            "renta ",
        )
    ):
        return ""
    if _JUNK_NAME_RE.match(text):
        return ""
    return text


def agent_from_text(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).strip()
    if not blob:
        return ""
    match = _AGENT_LINE_RE.search(blob)
    if not match:
        return ""
    name = _clean_person_name(match.group(1))
    if not name:
        return ""
    lower = name.lower()
    if any(w in lower for w in _AGENCY_WORDS):
        return ""
    return name


_JUNK_PERSON_PREFIXES = (
    "con ", "la ", "el ", "los ", "las ", "sin ", "para ", "una ", "un ",
    "de ", "en ", "tu ", "su ", "mis ", "este ", "esta ",
)


def _clean_person_name(text: str) -> str:
    cleaned = text.strip().strip(".,")
    if _JUNK_NAME_RE.match(cleaned):
        return ""
    if cleaned.lower().startswith(("colonia ", "departamento ", "casa ", "venta ", "renta ")):
        return ""
    lower = cleaned.lower()
    if any(lower.startswith(p) for p in _JUNK_PERSON_PREFIXES):
        return ""
    words = cleaned.split()
    if len(words) > 5 or len(cleaned) > 48:
        return ""
    return cleaned


def agency_from_text(*texts: str) -> str:
    blob = " ".join(t for t in texts if t).strip()
    if not blob:
        return ""

    for pattern in _AGENCY_PATTERNS:
        match = pattern.search(blob)
        if not match:
            continue
        cleaned = _clean_agency_name(match.group(1))
        if cleaned and len(cleaned) >= 4:
            return cleaned

    title_match = _TITLE_AGENCY_RE.search(blob)
    if title_match:
        candidate = _clean_agency_name(title_match.group(1))
        if candidate and "inmobiliaria" not in candidate.lower():
            candidate = f"{candidate} Inmobiliaria"
        if candidate:
            return candidate
    return ""


def extract_contacts_from_raw(raw: dict[str, Any]) -> tuple[str, str]:
    """Publisher/agency and agent from an Apify/portal payload."""
    publisher = _deep_find_name(raw, _PUBLISHER_KEYS)
    agent = _deep_find_name(raw, _AGENT_KEYS)

    if not publisher:
        publisher = agency_from_text(
            str(raw.get("title") or raw.get("headline") or ""),
            str(raw.get("description") or ""),
        )
    if not agent and publisher:
        # Some rows only expose the agency; agent stays empty.
        pass
    elif not agent:
        agent = agent_from_text(
            str(raw.get("description") or ""),
            str(raw.get("title") or raw.get("headline") or ""),
        ) or _deep_find_name(raw, ("asesor", "sellerName", "ownerName"))

    if agent and publisher and agent.lower() == publisher.lower():
        agent = ""
    return publisher, agent


def resolve_listing_contacts(row: dict[str, Any]) -> tuple[str, str]:
    """Best-effort publisher/agent for a normalized listing row."""
    publisher = str(row.get("publisher_name") or row.get("publisher") or "").strip()
    agent = str(row.get("agent_name") or "").strip()

    if publisher.startswith("{") or publisher.startswith("["):
        publisher = ""
    if agent.startswith("{") or agent.startswith("["):
        agent = ""

    if not publisher or not agent:
        from_raw_pub, from_raw_agent = extract_contacts_from_raw(row)
        publisher = publisher or from_raw_pub
        agent = agent or from_raw_agent

    if not agent:
        agent = agent_from_text(
            str(row.get("description") or ""),
            str(row.get("title") or ""),
        )

    if not publisher:
        publisher = agency_from_text(
            str(row.get("title") or ""),
            str(row.get("description") or ""),
        )

    if agent and publisher and agent.lower() == publisher.lower():
        agent = ""

    if agent and not _clean_person_name(agent):
        agent = ""

    return publisher, agent


def enrich_listing_contacts(row: dict[str, Any]) -> dict[str, Any]:
    """Fill publisher_name / agent_name on a listing when missing."""
    publisher, agent = resolve_listing_contacts(row)
    out = dict(row)
    if publisher:
        out["publisher_name"] = publisher
    if agent:
        out["agent_name"] = agent
    return out


def enrich_listings_contacts(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_listing_contacts(row) for row in listings]
