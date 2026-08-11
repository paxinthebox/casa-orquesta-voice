"""
Casa·Orquesta — Matching Service.

Hybrid filter + lightweight semantic re-rank. In production the re-rank uses
BGE-M3 + reranker. Here we use TF-IDF-like keyword scoring so the demo is
dependency-light and runs offline.

Phase 1.4 status: ported from MVP with no logic changes. When the Listings
service exposes embeddings via pgvector (Phase 1.4 listings work), this
service will additionally rerank using cosine similarity over those vectors.
The hybrid path is gated on whether candidates carry an `embedding` field
(graceful no-op when absent).
"""
import os
import math
import sys
from collections import Counter

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from listing_filters import (  # noqa: E402
    apply_type_filter,
    apply_types_filter,
    canonicalize_listing_row,
    is_demo_catalog_listing,
    is_live_listing,
    is_long_term_rent,
    listing_mode,
    normalize_type_filters,
)
from location_filters import filter_by_location  # noqa: E402
from money_speech import infer_listing_mode  # noqa: E402
from closeness import closeness_score  # noqa: E402
from fetch_params import build_listings_fetch_params, location_scoped_search  # noqa: E402
from near_match import (  # noqa: E402
    _scoped_pilot_states,
    apply_state_and_mode,
    apply_strict_filters,
    find_near_matches,
)
from source_diversity import diversify_by_source  # noqa: E402
from location_filters import row_in_locality_scope  # noqa: E402

LISTINGS_URL = os.getenv("LISTINGS_URL", "http://listings:8000")
MATCHING_CANDIDATE_LIMIT = int(os.getenv("MATCHING_CANDIDATE_LIMIT", "800"))
MATCHING_MIN_CLOSENESS = float(os.getenv("MATCHING_MIN_CLOSENESS", "22"))
STRICT_MATCH_BONUS = float(os.getenv("MATCHING_STRICT_BONUS", "12"))


app = FastAPI(title="Casa·Orquesta Matching", version="0.1.0")


class SearchReq(BaseModel):
    filters: dict = Field(default_factory=dict)
    query: str = ""
    k: int = 8


def tokenize(s: str) -> list[str]:
    s = s.lower()
    return [t for t in "".join(c if c.isalnum() else " " for c in s).split() if len(t) > 2]


def listing_text(L: dict) -> str:
    parts = [
        L.get("title", ""), L.get("description", ""),
        L.get("neighborhood", ""), L.get("city", ""), L.get("state", ""),
        " ".join(L.get("features", [])), L.get("type", ""),
    ]
    return " ".join(parts)


def score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    qc = Counter(query_tokens)
    dc = Counter(doc_tokens)
    overlap = sum(min(qc[t], dc[t]) for t in qc)
    return overlap / math.sqrt(len(query_tokens) * len(doc_tokens))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


@app.get("/health")
def health():
    return {"status": "ok"}


def _is_live(row: dict) -> bool:
    return is_live_listing(row)


async def _fetch_listings(client: httpx.AsyncClient, params: dict) -> list[dict]:
    r = await client.get(f"{LISTINGS_URL}/listings", params=params)
    r.raise_for_status()
    rows = r.json()
    return rows if isinstance(rows, list) else []


def _apply_type_filter(candidates: list[dict], want_type: str) -> list[dict]:
    return apply_type_filter(candidates, want_type)


def _apply_types_filter(candidates: list[dict], want_types: list[str]) -> list[dict]:
    return apply_types_filter(candidates, want_types)


def _apply_location_filter(
    candidates: list[dict],
    *,
    filters: dict,
    query: str,
) -> list[dict]:
    return filter_by_location(candidates, filters=filters, query=query)


def _apply_price_max_filter(candidates: list[dict], price_max: int) -> list[dict]:
    if price_max <= 0:
        return candidates
    return [
        c for c in candidates
        if isinstance(c.get("price_mxn"), (int, float))
        and 0 < int(c["price_mxn"]) <= price_max
    ]


def _apply_beds_min_filter(candidates: list[dict], beds_min: int) -> list[dict]:
    if beds_min <= 0:
        return candidates
    return [
        c for c in candidates
        if isinstance(c.get("beds"), (int, float)) and int(c["beds"]) >= beds_min
    ]


def _apply_baths_min_filter(candidates: list[dict], baths_min: int) -> list[dict]:
    if baths_min <= 0:
        return candidates
    out: list[dict] = []
    for c in candidates:
        baths = c.get("baths") or c.get("bathrooms")
        if not isinstance(baths, (int, float)) or float(baths) <= 0:
            out.append(c)
        elif float(baths) >= baths_min:
            out.append(c)
    return out


def _location_scoped_search(filters: dict, query: str) -> bool:
    return location_scoped_search(filters, query)


@app.post("/match/search")
async def search(req: SearchReq):
    # 1. Hard filter via Listings service
    f = normalize_type_filters(dict(req.filters or {}))
    if not f.get("listing_mode"):
        inferred = infer_listing_mode(req.query)
        if inferred:
            f["listing_mode"] = inferred
    candidate_limit = MATCHING_CANDIDATE_LIMIT
    if f.get("listing_mode") == "rent":
        candidate_limit = max(
            candidate_limit,
            int(os.getenv("MATCHING_RENT_CANDIDATE_LIMIT", "400")),
        )
    if f.get("mortgage"):
        candidate_limit = max(
            MATCHING_CANDIDATE_LIMIT,
            int(os.getenv("MATCHING_MORTGAGE_CANDIDATE_LIMIT", "800")),
        )
    params: dict = build_listings_fetch_params(
        f, query=req.query, candidate_limit=candidate_limit,
    )
    pilot_states = f.get("states")
    if isinstance(pilot_states, list):
        pilot_states = _scoped_pilot_states(
            pilot_states, filters=f, query=req.query,
        )
        if pilot_states and len(pilot_states) == 1:
            f = dict(f)
            f["state"] = pilot_states[0]
            f.pop("states", None)
            pilot_states = None

    match_quality = "exact"
    relaxations: list[str] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        candidates = await _fetch_listings(client, params)

        # SQL type filter can miss live rows (legacy ingest used type=inmueble).
        # If we only got demo seed, relax type and keep state.
        if f.get("type") and candidates and not any(_is_live(c) for c in candidates):
            relaxed = {k: v for k, v in params.items() if k != "type"}
            retry = await _fetch_listings(client, relaxed)
            live_retry = [c for c in retry if _is_live(c)]
            if live_retry:
                candidates = _apply_type_filter(live_retry, str(f["type"]))

        live = [c for c in candidates if _is_live(c)]
        if live:
            want_mode = (f.get("listing_mode") or "").strip().lower()
            if want_mode == "rent":
                live_rent = [
                    c for c in live
                    if listing_mode(c) == "rent" and is_long_term_rent(c)
                ]
                if live_rent:
                    candidates = live_rent
                # else: keep seed/demo rent rows returned by listings service
            else:
                candidates = live

        if (f.get("listing_mode") or "").strip().lower() == "rent":
            if any(_is_live(c) and listing_mode(c) == "rent" for c in candidates):
                candidates = [
                    c for c in candidates
                    if not is_demo_catalog_listing(c)
                ]

        ranking_pool = apply_state_and_mode(
            candidates,
            filters=f,
            pilot_states=pilot_states if isinstance(pilot_states, list) else None,
        )
        strict = apply_strict_filters(
            ranking_pool,
            filters=f,
            query=req.query,
            pilot_states=pilot_states if isinstance(pilot_states, list) else None,
        )
        near_pool, near_relax = find_near_matches(
            candidates,
            filters=f,
            query=req.query,
            pilot_states=pilot_states if isinstance(pilot_states, list) else None,
            limit=max(req.k, int(os.getenv("MATCHING_NEAR_MIN_RESULTS", "3"))) * 3,
        )

        strict_ids = {c["id"] for c in strict if c.get("id")}
        near_relax_set = set(near_relax)
        city_widen = "location" in near_relax_set
        candidate_map: dict[str, dict] = {}

        for row in strict:
            rid = row.get("id")
            if rid:
                candidate_map[rid] = row

        for row in near_pool:
            rid = row.get("id")
            if not rid or rid in candidate_map:
                continue
            if not row_in_locality_scope(
                row,
                filters=f,
                query=req.query,
                allow_city_widen=city_widen,
            ):
                continue
            candidate_map[rid] = row

        scored: list[dict] = []
        query_embedding = f.get("query_embedding") or []
        for row in candidate_map.values():
            rid = row.get("id")
            is_strict = bool(rid and rid in strict_ids)
            rel = set() if is_strict else near_relax_set
            row_score = closeness_score(
                row, filters=f, query=req.query, relaxations=rel,
            )
            if is_strict:
                row_score = min(100.0, row_score + STRICT_MATCH_BONUS)
            elif row_score < MATCHING_MIN_CLOSENESS:
                continue
            emb = row.get("embedding")
            if query_embedding and emb:
                row_score = min(100.0, row_score + 5.0 * cosine(query_embedding, emb))
            row["_score"] = row_score
            row["_strict"] = is_strict
            scored.append(row)

        scored.sort(
            key=lambda x: (x.get("_score", 0), x.get("_strict", False)),
            reverse=True,
        )
        candidates = diversify_by_source(scored, req.k)

        if not candidates:
            match_quality = "none"
            relaxations = near_relax
        elif strict_ids and all(c.get("id") in strict_ids for c in candidates):
            match_quality = "exact"
            relaxations = []
        elif strict_ids:
            match_quality = "near" if any(c.get("id") not in strict_ids for c in candidates) else "exact"
            relaxations = near_relax if match_quality == "near" else []
        else:
            match_quality = "near"
            relaxations = near_relax

        # Hard budget envelope on the final card set (defense in depth).
        price_max = f.get("price_max_mxn")
        if price_max and candidates:
            cap = int(price_max)
            if match_quality == "near" and "price_max_mxn" in set(relaxations):
                factor = float(os.getenv("MATCHING_NEAR_PRICE_FACTOR", "1.5"))
                ceiling = int(cap * factor)
            else:
                ceiling = cap
            candidates = [
                c for c in candidates
                if isinstance(c.get("price_mxn"), (int, float))
                and 0 < int(c["price_mxn"]) <= ceiling
            ]
            if not candidates:
                match_quality = "none"

    # Project to a clean response shape
    results = []
    for c in candidates[: req.k]:
        row = canonicalize_listing_row(c)
        mode = listing_mode(row)
        results.append({
            "id": row["id"], "title": row.get("title", ""), "address": row.get("address", row.get("title", "")),
            "state": row.get("state"), "city": row.get("city"), "neighborhood": row.get("neighborhood"),
            "fraccionamiento": row.get("fraccionamiento"),
            "price_mxn": row.get("price_mxn"),
            "beds": row.get("beds"), "bedrooms": row.get("beds") or row.get("bedrooms"),
            "baths": row.get("baths"), "bathrooms": row.get("baths") or row.get("bathrooms"),
            "m2": row.get("m2"), "type": row.get("type"), "features": row.get("features", []),
            "listing_mode": mode,
            "description": row.get("description"), "score": round(c.get("_score", 0), 3),
            "media": row.get("media", []),
            "thumbnail": row.get("thumbnail") or (
                (row.get("media") or [None])[0] if row.get("media") else None
            ),
            "source": row.get("source"),
            "source_url": row.get("source_url"),
            "alternate_sources": row.get("alternate_sources") or [],
            "year_built": row.get("year_built"),
            "status": row.get("status"),
            "lat": row.get("lat"),
            "lng": row.get("lng"),
            "publisher_name": row.get("publisher_name"),
            "agent_name": row.get("agent_name"),
            "rent_term": row.get("rent_term"),
            "accepts_financing": row.get("accepts_financing") or [],
            "match_kind": match_quality,
        })
    return {
        "count": len(results),
        "results": results,
        "match_quality": match_quality,
        "relaxations": relaxations,
    }
