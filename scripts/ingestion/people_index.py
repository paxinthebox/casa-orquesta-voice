"""Build collaborator/broker index from ingested listing publisher metadata."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .contact_extract import enrich_listings_contacts, resolve_listing_contacts


def _slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return clean[:48] or "unknown"


def _source_prefix(source: str) -> str:
    src = source.lower().strip()
    if src == "inmuebles24":
        return "I24"
    if src == "vivanuncios":
        return "VA"
    if src == "propiedades":
        return "PROP"
    if src == "lamudi":
        return "LAM"
    if src.startswith("easybroker"):
        return "EB"
    return "MKT"


def _merge_people(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge by id; enrich neighborhoods/firm on duplicate ids from incoming."""
    by_id: dict[str, dict[str, Any]] = {}
    for person in existing:
        pid = str(person.get("id") or "")
        if pid:
            by_id[pid] = dict(person)
    for person in incoming:
        pid = str(person.get("id") or "")
        if not pid:
            continue
        if pid not in by_id:
            by_id[pid] = dict(person)
            continue
        cur = by_id[pid]
        for hood in person.get("neighborhoods") or []:
            hoods = cur.setdefault("neighborhoods", [])
            if hood and hood not in hoods:
                hoods.append(hood)
        if not cur.get("city") and person.get("city"):
            cur["city"] = person["city"]
        if not cur.get("firm") and person.get("firm"):
            cur["firm"] = person["firm"]
        if not cur.get("source_url") and person.get("source_url"):
            cur["source_url"] = person["source_url"]
    return list(by_id.values())


def people_from_listings(listings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract brokers (publishers/agencies) and collaborators (named agents) from listings."""
    collaborators: dict[str, dict[str, Any]] = {}
    brokers: dict[str, dict[str, Any]] = {}

    for row in listings:
        source = str(row.get("source") or "market")
        prefix = _source_prefix(source)
        state = str(row.get("state") or "CDMX")
        city = str(row.get("city") or "")
        neighborhood = str(row.get("neighborhood") or "")

        publisher, agent_name = resolve_listing_contacts(row)
        if publisher:
            key = _slug(publisher)
            bid = f"{prefix}-BR-{key}"
            if bid not in brokers:
                brokers[bid] = {
                    "id": bid,
                    "name": publisher,
                    "firm": publisher,
                    "state": state,
                    "city": city,
                    "neighborhoods": [neighborhood] if neighborhood else [],
                    "specialties": ["portal público", source],
                    "license": "publicación en línea",
                    "rating": 4.5,
                    "source": source,
                    "source_url": row.get("source_url") or "",
                }
            else:
                if neighborhood and neighborhood not in brokers[bid]["neighborhoods"]:
                    brokers[bid]["neighborhoods"].append(neighborhood)
                if city and not brokers[bid].get("city"):
                    brokers[bid]["city"] = city

        if agent_name and agent_name.lower() != publisher.lower():
            akey = _slug(agent_name)
            aid = f"{prefix}-CA-{akey}"
            if aid not in collaborators:
                collaborators[aid] = {
                    "id": aid,
                    "name": agent_name,
                    "state": state,
                    "city": city,
                    "neighborhoods": [neighborhood] if neighborhood else [],
                    "specialties": ["publicación en línea", source],
                    "languages": ["es"],
                    "availability": "this_week",
                    "rating": 4.6,
                    "source": source,
                    "firm": publisher or "",
                    "source_url": row.get("source_url") or "",
                }

    return list(collaborators.values()), list(brokers.values())


def build_market_people_index(
    listings: list[dict[str, Any]],
    *,
    eb_collaborators: list[dict[str, Any]] | None = None,
    eb_brokers: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    enriched = enrich_listings_contacts(listings)
    market_colabs, market_brokers = people_from_listings(enriched)
    collaborators = _merge_people(eb_collaborators or [], market_colabs)
    brokers = _merge_people(eb_brokers or [], market_brokers)
    return collaborators, brokers


def stable_listing_id(source: str, external_id: str) -> str:
    if external_id:
        return f"{source.upper()[:3]}-{external_id}"
    digest = hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:10]
    return f"{source.upper()[:3]}-{digest}"
