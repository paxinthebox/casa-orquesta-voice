"""Matching must not drop seed rent inventory when live catalog is sale-only."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
SHARED = os.path.normpath(os.path.join(SVC, "..", "_shared"))
sys.path.insert(0, SVC)
sys.path.insert(0, SHARED)

from listing_filters import apply_listing_mode_filter, is_live_listing  # noqa: E402
from money_speech import infer_listing_mode  # noqa: E402


def test_infer_listing_mode_from_rent_query():
    assert infer_listing_mode("departamento en renta Roma Norte hasta 35000 al mes") == "rent"


def test_rent_query_without_filter_key():
    """Rent intent in query must set listing_mode before live-only preference."""
    rows = [
        {"id": "I24-1", "source": "inmuebles24", "listing_mode": "sale", "state": "CDMX"},
        {"id": "L-CDMX-R001", "listing_mode": "rent", "state": "CDMX", "rent_term": "long"},
    ]
    mode = infer_listing_mode("departamento en renta Roma Norte")
    assert mode == "rent"
    out = _simulate_post_fetch_candidates(rows, listing_mode=mode)
    assert [r["id"] for r in out] == ["L-CDMX-R001"]


def _simulate_post_fetch_candidates(rows: list[dict], *, listing_mode: str) -> list[dict]:
    """Mirror matching/main.py live-preference for rent."""

    def _is_live(row: dict) -> bool:
        return is_live_listing(row)

    live = [c for c in rows if _is_live(c)]
    if live:
        if listing_mode == "rent":
            live_rent = [
                c for c in live
                if str(c.get("listing_mode") or "sale").lower() == "rent"
            ]
            if live_rent:
                return live_rent
        else:
            return live
    return apply_listing_mode_filter(rows, listing_mode)


def test_rent_keeps_seed_when_live_pool_is_sale_only():
    rows = [
        {"id": "I24-1", "source": "inmuebles24", "listing_mode": "sale", "state": "CDMX"},
        {"id": "L-CDMX-R001", "listing_mode": "rent", "state": "CDMX", "rent_term": "long"},
    ]
    out = _simulate_post_fetch_candidates(rows, listing_mode="rent")
    assert [r["id"] for r in out] == ["L-CDMX-R001"]
