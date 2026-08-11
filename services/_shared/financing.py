"""Financing acceptance extraction and search filters (INFONAVIT, FOVISSSTE, …)."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

FINANCING_TYPES = frozenset({
    "INFONAVIT",
    "FOVISSSTE",
    "bancario",
    "cofinanciamiento",
    "contado",
})

# Query mortgage value → listing tags that satisfy it.
_MORTGAGE_SATISFIERS: dict[str, frozenset[str]] = {
    "INFONAVIT": frozenset({"INFONAVIT", "cofinanciamiento"}),
    "FOVISSSTE": frozenset({"FOVISSSTE", "cofinanciamiento"}),
    "bancario": frozenset({"bancario", "cofinanciamiento"}),
    "cofinanciamiento": frozenset({"cofinanciamiento", "INFONAVIT", "FOVISSSTE", "bancario"}),
    "contado": frozenset({"contado"}),
}

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("INFONAVIT", re.compile(r"infonavit|cofinavit", re.I)),
    ("FOVISSSTE", re.compile(r"fovis+sste|fovissste", re.I)),
    ("cofinanciamiento", re.compile(r"cofinanciamiento|cofinavit", re.I)),
    (
        "bancario",
        re.compile(
            r"cr[eé]dito\s+bancario|cr[eé]ditoses?\s+bancarios?|hipoteca\s+bancaria|"
            r"acepta\s+cr[eé]ditos?\s+bancarios?|\bbancarios?\b",
            re.I,
        ),
    ),
    (
        "contado",
        re.compile(
            r"pago\s+de\s+contado|solo\s+contado|solamente\s+contado|"
            r"unicamente\s+contado|únicamente\s+contado|precios?\s+especiales.*contado",
            re.I,
        ),
    ),
)

_GENERIC_CREDIT_RE = re.compile(
    r"acepta\s+cr[eé]ditos?|se\s+acepta\s+cr[eé]dito|apta\s+para\s+cr[eé]ditos?",
    re.I,
)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def listing_financing_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("title") or ""),
        str(row.get("description") or ""),
        " ".join(str(x) for x in (row.get("features") or [])),
    ]
    return " ".join(parts)


def extract_accepts_financing(text: str) -> list[str]:
    """Parse listing copy → canonical financing tags (deduped, stable order)."""
    if not text or not text.strip():
        return []

    found: list[str] = []
    for tag, pattern in _PATTERNS:
        if pattern.search(text) and tag not in found:
            found.append(tag)

    if not found and _GENERIC_CREDIT_RE.search(text):
        found.append("bancario")

    return found


def enrich_listing_financing(row: dict[str, Any]) -> dict[str, Any]:
    """Set accepts_financing on a listing row (in-place)."""
    existing = row.get("accepts_financing")
    if isinstance(existing, list) and existing:
        row["accepts_financing"] = [t for t in existing if t in FINANCING_TYPES]
        return row

    tags = extract_accepts_financing(listing_financing_text(row))
    if tags:
        row["accepts_financing"] = tags
    else:
        row.pop("accepts_financing", None)
    return row


def enrich_listings_financing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        enrich_listing_financing(row)
    return rows


def listing_accepts_mortgage(row: dict[str, Any], mortgage: str) -> bool:
    """True when listing satisfies a buyer mortgage filter (e.g. INFONAVIT)."""
    want = str(mortgage or "").strip()
    if not want:
        return True

    satisfiers = _MORTGAGE_SATISFIERS.get(want) or frozenset({want})
    accepted = row.get("accepts_financing")
    if not isinstance(accepted, list) or not accepted:
        accepted = extract_accepts_financing(listing_financing_text(row))
    if not accepted:
        return False
    return bool(set(accepted) & satisfiers)


def apply_financing_filter(
    rows: list[dict[str, Any]],
    mortgage: str | None,
) -> list[dict[str, Any]]:
    want = str(mortgage or "").strip()
    if not want:
        return rows
    return [r for r in rows if listing_accepts_mortgage(r, want)]
