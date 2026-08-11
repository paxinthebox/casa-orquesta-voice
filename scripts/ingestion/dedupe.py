"""Dedupe listings from multiple market sources before pushing to the catalog."""
from __future__ import annotations

import hashlib
import re
from typing import Any

_SOURCE_PRIORITY = {
    "easybroker": 0,
    "easybroker_mls": 0,
    "inmuebles24": 1,
    "vivanuncios": 1,
    "propiedades": 1,
    "lamudi": 2,
    "mercadolibre": 3,
    "seed": 9,
}


def _norm_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def listing_fingerprint(row: dict[str, Any]) -> str:
    """Stable hash for cross-portal duplicate detection."""
    parts = [
        _norm_text(str(row.get("state", ""))),
        _norm_text(str(row.get("city", ""))),
        _norm_text(str(row.get("neighborhood", ""))),
        str(int(row.get("price_mxn") or 0)),
        str(int(row.get("beds") or 0)),
        str(int(float(row.get("m2") or 0))),
    ]
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _source_ref(row: dict[str, Any]) -> dict[str, str]:
    return {
        "source": str(row.get("source") or ""),
        "id": str(row.get("id") or ""),
        "source_url": str(row.get("source_url") or ""),
    }


def _attach_alternate(holder: dict[str, Any], other: dict[str, Any]) -> None:
    """Record a syndicated portal listing without keeping a duplicate card."""
    ref = _source_ref(other)
    if not ref["id"] or ref["id"] == str(holder.get("id") or ""):
        return
    alts = holder.setdefault("alternate_sources", [])
    if not isinstance(alts, list):
        alts = []
        holder["alternate_sources"] = alts
    if any(str(a.get("id") or "") == ref["id"] for a in alts if isinstance(a, dict)):
        return
    alts.append(ref)


def _pick_winner(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_rank = _SOURCE_PRIORITY.get(str(existing.get("source") or ""), 5)
    incoming_rank = _SOURCE_PRIORITY.get(str(incoming.get("source") or ""), 5)
    if incoming_rank < existing_rank:
        return incoming
    if incoming_rank > existing_rank:
        return existing
    if len(str(incoming.get("description") or "")) > len(
        str(existing.get("description") or "")
    ):
        return incoming
    return existing


def dedupe_listings(listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-priority source when the same property appears twice."""
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for row in listings:
        fp = listing_fingerprint(row)

        if fp not in best:
            best[fp] = dict(row)
            order.append(fp)
            continue

        existing = best[fp]
        winner = _pick_winner(existing, row)
        loser = row if winner is existing else existing
        merged = dict(winner)
        for alt in existing.get("alternate_sources") or []:
            if isinstance(alt, dict):
                _attach_alternate(merged, alt)
        for alt in row.get("alternate_sources") or []:
            if isinstance(alt, dict):
                _attach_alternate(merged, alt)
        _attach_alternate(merged, loser)
        best[fp] = merged

    return [best[fp] for fp in order if fp in best]
