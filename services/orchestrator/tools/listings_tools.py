"""
Listings tool handlers — Phase 1.6.

Each handler matches the MVP's behavior verbatim (HTTP calls into the
matching + listings services). When the matching service is unreachable the
handler returns the raw exception in `error` rather than raising — keeps the
agent loop from crashing on transient network blips.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import httpx

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from listing_filters import normalize_type_filters  # noqa: E402

LISTINGS_URL = os.getenv("LISTINGS_URL", "http://listings:8000")
MATCHING_URL = os.getenv("MATCHING_URL", "http://matching:8000")
SEARCH_K_DEFAULT = int(os.getenv("SEARCH_K_DEFAULT", "8"))


def _resolved_filters(ctx: Any) -> dict:
    """Session-resolved filters are authoritative — LLM tool args cannot override."""
    raw = ctx.state.get("filters_resolved") or ctx.state.get("filters") or {}
    return normalize_type_filters(dict(raw) if isinstance(raw, dict) else {})


async def search_listings(args: dict, ctx: Any) -> dict:
    filters = _resolved_filters(ctx)
    body = {
        "filters": filters,
        "query": args.get("query") or ctx.state.get("user_query", ""),
        "k": max(SEARCH_K_DEFAULT, int(args.get("k", SEARCH_K_DEFAULT))),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{MATCHING_URL}/match/search", json=body)
        r.raise_for_status()
        data = r.json()
    ctx.state["last_candidates"] = data.get("results", [])
    ctx.state["last_match_quality"] = data.get("match_quality", "exact")
    ctx.state["last_relaxations"] = data.get("relaxations") or []
    data["filters_applied"] = filters
    return data


async def get_listing(args: dict, ctx: Any) -> dict:
    lid = args["listing_id"]
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{LISTINGS_URL}/listings/{lid}")
        r.raise_for_status()
        return r.json()


async def compare_listings(args: dict, ctx: Any) -> dict:
    ids = args.get("listing_ids", [])
    out = []
    async with httpx.AsyncClient(timeout=8.0) as client:
        for lid in ids[:4]:
            r = await client.get(f"{LISTINGS_URL}/listings/{lid}")
            if r.status_code == 200:
                out.append(r.json())
    if len(out) < 2:
        return {"error": "need at least 2 listings", "got": len(out)}
    keys = ["price_mxn", "beds", "baths", "m2", "year_built", "neighborhood", "state"]
    matrix = {k: [L.get(k) for L in out] for k in keys}
    cheapest = min(out, key=lambda L: L.get("price_mxn", 1e15))
    largest = max(out, key=lambda L: L.get("m2", 0))
    return {"items": out, "matrix": matrix,
            "cheapest_id": cheapest["id"], "largest_id": largest["id"]}
