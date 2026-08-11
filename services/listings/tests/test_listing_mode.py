"""Rent vs sale listing selection — stage catalog has seed rent, ingest is mostly sale."""
from __future__ import annotations

import os

_LIVE_SOURCES = frozenset({"inmuebles24", "easybroker", "easybroker_mls", "lamudi"})


def _is_live_listing(row: dict) -> bool:
    source = str(row.get("source") or "")
    lid = str(row.get("id") or "")
    return source in _LIVE_SOURCES or lid.startswith(("I24-", "EB-", "EB-MLS-"))


def _has_live_rent_catalog() -> bool:
    return os.getenv("LISTINGS_HAS_LIVE_RENT", "").strip().lower() in {
        "1", "true", "yes",
    }


def _prefer_live_listings(
    rows: list[dict],
    *,
    listing_mode: str | None = None,
) -> list[dict]:
    live = [r for r in rows if _is_live_listing(r)]
    mode = (listing_mode or "").strip().lower()
    if mode == "rent":
        live_rent = [
            r for r in live
            if str(r.get("listing_mode") or "sale").lower() == "rent"
        ]
        if live_rent:
            return live_rent
        if _has_live_rent_catalog():
            return []
        rent_rows = [
            r for r in rows
            if str(r.get("listing_mode") or "sale").lower() == "rent"
        ]
        return rent_rows if rent_rows else rows
    if not live:
        return rows
    return live


def test_rent_search_keeps_seed_when_no_live_rent():
    rows = [
        {"id": "I24-1", "source": "inmuebles24", "listing_mode": "sale", "title": "Venta"},
        {"id": "L-CDMX-R001", "listing_mode": "rent", "title": "Renta Roma"},
    ]
    out = _prefer_live_listings(rows, listing_mode="rent")
    assert [r["id"] for r in out] == ["L-CDMX-R001"]


def test_rent_search_uses_live_rent_when_present():
    rows = [
        {"id": "I24-rent", "source": "inmuebles24", "listing_mode": "rent", "title": "Live rent"},
        {"id": "L-CDMX-R001", "listing_mode": "rent", "title": "Seed rent"},
        {"id": "I24-sale", "source": "inmuebles24", "listing_mode": "sale", "title": "Live sale"},
    ]
    out = _prefer_live_listings(rows, listing_mode="rent")
    assert [r["id"] for r in out] == ["I24-rent"]


def test_rent_search_empty_when_live_catalog_loaded_but_filtered_out():
    os.environ["LISTINGS_HAS_LIVE_RENT"] = "1"
    try:
        rows = [
            {"id": "L-MOR-R003", "listing_mode": "rent", "title": "Seed Cuernavaca"},
        ]
        out = _prefer_live_listings(rows, listing_mode="rent")
        assert out == []
    finally:
        os.environ.pop("LISTINGS_HAS_LIVE_RENT", None)


def test_sale_search_still_prefers_live():
    rows = [
        {"id": "I24-1", "source": "inmuebles24", "listing_mode": "sale"},
        {"id": "L-001", "listing_mode": "sale"},
    ]
    out = _prefer_live_listings(rows, listing_mode="sale")
    assert [r["id"] for r in out] == ["I24-1"]
