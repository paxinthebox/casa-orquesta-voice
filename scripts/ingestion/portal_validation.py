"""Per-portal ingest quality checks — geo accuracy, colonia hit rate, scrape health."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from typing import Any

from .market_geo import (
    CDMX_MARKET_BOROUGHS,
    MORELOS_MARKET_CITIES,
    _colonia_aliases,
    _fold,
    _listing_matches_colonia,
    listing_in_market,
    matches_market_query,
    parse_location_string,
    resolve_listing_state,
)


def query_key(query: dict[str, Any]) -> str:
    """Stable key for cross-portal comparison of the same market query."""
    parts = (
        query.get("state"),
        query.get("city") or query.get("municipality"),
        query.get("colonia"),
        query.get("propertyType"),
        query.get("transactionType"),
        query.get("scope"),
    )
    return "|".join(str(part or "") for part in parts)


def _preview_row(flat: dict[str, Any]) -> dict[str, Any]:
    loc = flat.get("posting_location")
    location_text = ""
    if isinstance(flat.get("location"), str):
        location_text = flat["location"]
    elif isinstance(loc, dict):
        inner = loc.get("location")
        if isinstance(inner, dict):
            location_text = str(inner.get("name") or "")

    nbhd, loc_city, _ = parse_location_string(location_text)
    neighborhood = str(
        flat.get("neighborhood")
        or flat.get("postingLocation_location_name")
        or nbhd
        or ""
    ).strip()
    city = str(
        flat.get("city")
        or flat.get("postingLocation_location_parent_name")
        or loc_city
        or ""
    ).strip()

    if isinstance(loc, dict) and not neighborhood:
        inner = loc.get("location")
        if isinstance(inner, dict):
            neighborhood = str(inner.get("name") or "").strip()
            parent = inner.get("parent")
            if isinstance(parent, dict) and not city:
                city = str(parent.get("name") or "").strip()

    title = str(flat.get("title") or flat.get("headline") or "")
    description = str(flat.get("description") or flat.get("description_normalized") or "")
    address = str(flat.get("address") or flat.get("full_address") or "")

    if not city:
        city = _infer_city_from_text(title, description, address, location_text)

    state = resolve_listing_state(
        raw_state=str(flat.get("state") or ""),
        city=city,
        location_text=location_text or address,
    )
    if not state:
        state = _infer_state_from_text(title, description, address, location_text)

    return {
        "title": title[:96],
        "neighborhood": neighborhood,
        "city": city,
        "state": state,
        "description": description[:200],
        "location": location_text,
    }


def _infer_city_from_text(*parts: str) -> str:
    blob = _fold(" ".join(p for p in parts if p))
    if not blob:
        return ""
    cities = sorted(
        (*MORELOS_MARKET_CITIES, *CDMX_MARKET_BOROUGHS),
        key=len,
        reverse=True,
    )
    for city in cities:
        folded = _fold(city)
        if not folded:
            continue
        if re.search(rf"(?:^|[\s,.]){re.escape(folded)}(?:[\s,.]|$)", blob):
            return city
    return ""


def _infer_state_from_text(*parts: str) -> str:
    blob = _fold(" ".join(p for p in parts if p))
    if not blob:
        return ""
    if any(tok in blob for tok in (" morelos", ",morelos", "mor.", "-mor")):
        return "Morelos"
    if any(
        tok in blob
        for tok in (
            " cdmx",
            ",cdmx",
            "ciudad de mexico",
            "ciudad de méxico",
            "distrito federal",
        )
    ):
        return "CDMX"
    city = _infer_city_from_text(*parts)
    if city:
        return resolve_listing_state(raw_state="", city=city, location_text="")
    return ""


def classify_rejection(
    flat: dict[str, Any],
    query: dict[str, Any],
    *,
    normalize_fn: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any] | None],
) -> str:
    """Best-effort reason a raw portal row did not survive sanitize."""
    if normalize_fn(flat, query):
        return "kept"

    preview = _preview_row(flat)
    has_id = bool(
        flat.get("postingId")
        or flat.get("posting_id")
        or flat.get("url")
        or flat.get("link")
    )
    if not has_id:
        return "no_posting_id"

    expected_state = str(query.get("state") or "CDMX")
    state = preview["state"] or expected_state
    row = {
        **preview,
        "state": state,
        "address": str(flat.get("address") or ""),
    }

    if not listing_in_market(row):
        return "out_of_market"

    if not matches_market_query(row, query):
        scope = str(query.get("scope") or "").lower()
        if scope == "colonia" and not _listing_matches_colonia(row, query):
            return "wrong_colonia"
        return "geo_mismatch"

    return "sanitize_rejected"


def validate_query_batch(
    *,
    portal: str,
    query: dict[str, Any],
    raw_rows: list[dict[str, Any]],
    kept_rows: list[dict[str, Any]],
    search_url: str = "",
    error: str | None = None,
    flatten_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    normalize_fn: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Build validation report for one portal × one market query."""
    flatten = flatten_fn or (lambda row: row)
    key = query_key(query)
    scope = str(query.get("scope") or "").lower()
    raw_count = len(raw_rows)
    kept_count = len(kept_rows)

    rejection_reasons: Counter[str] = Counter()
    raw_colonia_hits = 0
    raw_in_market = 0
    sample_rejected: list[dict[str, Any]] = []

    if normalize_fn:
        for raw in raw_rows:
            flat = flatten(raw)
            preview = _preview_row(flat)
            probe = {**preview, "state": preview["state"]}
            if listing_in_market(probe):
                raw_in_market += 1
            if scope == "colonia" and _listing_matches_colonia(probe, query):
                raw_colonia_hits += 1

            reason = classify_rejection(flat, query, normalize_fn=normalize_fn)
            if reason != "kept":
                rejection_reasons[reason] += 1
                if len(sample_rejected) < 3:
                    sample_rejected.append({
                        "title": preview["title"],
                        "city": preview["city"],
                        "neighborhood": preview["neighborhood"],
                        "reason": reason,
                    })

    kept_colonia_hits = 0
    if scope == "colonia":
        kept_colonia_hits = sum(
            1 for row in kept_rows if _listing_matches_colonia(row, query)
        )

    raw_colonia_rate = _rate(raw_colonia_hits, raw_count)
    raw_in_market_rate = _rate(raw_in_market, raw_count)
    kept_colonia_rate = _rate(kept_colonia_hits, kept_count)

    issues: list[str] = []
    status = "pass"

    if error:
        status = "fail"
        issues.append(f"scrape error: {error}")
    elif raw_count == 0:
        status = "fail"
        issues.append("scrape returned 0 rows")
    elif scope == "colonia" and kept_count == 0 and raw_count >= 5:
        status = "fail"
        issues.append(
            f"colonia query kept 0/{raw_count} rows — portal likely returned off-area listings"
        )
    elif scope == "colonia" and kept_count > 0 and kept_colonia_rate < 0.5:
        status = "warn"
        issues.append(
            f"only {kept_colonia_rate:.0%} of kept rows match colonia aliases"
        )
    elif raw_count >= 10 and raw_in_market_rate < 0.3 and kept_count == 0:
        status = "warn"
        issues.append(
            f"only {raw_in_market_rate:.0%} of raw rows are in CDMX/Morelos pilot"
        )
    elif scope == "colonia" and raw_count >= 5 and raw_colonia_rate < 0.2:
        status = "warn"
        issues.append(
            f"only {raw_colonia_rate:.0%} of raw rows mention target colonia"
        )

    sample_kept = [
        {
            "title": str(row.get("title") or "")[:96],
            "neighborhood": row.get("neighborhood"),
            "city": row.get("city"),
            "price_mxn": row.get("price_mxn"),
        }
        for row in kept_rows[:3]
    ]

    return {
        "portal": portal,
        "query_key": key,
        "search_url": search_url,
        "status": status,
        "raw_count": raw_count,
        "kept_count": kept_count,
        "filtered_out": max(0, raw_count - kept_count),
        "raw_in_market_rate": raw_in_market_rate,
        "raw_colonia_hit_rate": raw_colonia_rate if scope == "colonia" else None,
        "kept_colonia_hit_rate": kept_colonia_rate if scope == "colonia" else None,
        "rejection_reasons": dict(rejection_reasons),
        "issues": issues,
        "sample_kept": sample_kept,
        "sample_rejected": sample_rejected,
    }


def summarize_portal(meta: dict[str, Any]) -> dict[str, Any]:
    """Aggregate validation stats for one portal source block in the manifest."""
    if meta.get("skipped"):
        return {
            "portal": meta.get("source", "unknown"),
            "skipped": True,
            "reason": meta.get("reason"),
        }

    portal = str(meta.get("source") or "unknown")
    query_reports = [
        q.get("validation")
        for q in meta.get("queries") or []
        if isinstance(q, dict) and isinstance(q.get("validation"), dict)
    ]
    if not query_reports:
        return {"portal": portal, "query_count": 0, "status": "unknown"}

    status_counts = Counter(str(r.get("status") or "unknown") for r in query_reports)
    kept_total = sum(int(r.get("kept_count") or 0) for r in query_reports)
    raw_total = sum(int(r.get("raw_count") or 0) for r in query_reports)

    overall = "pass"
    if status_counts.get("fail"):
        overall = "fail"
    elif status_counts.get("warn"):
        overall = "warn"

    failed_queries = [
        {
            "query_key": r.get("query_key"),
            "search_url": r.get("search_url"),
            "issues": r.get("issues"),
            "raw_count": r.get("raw_count"),
            "kept_count": r.get("kept_count"),
        }
        for r in query_reports
        if r.get("status") == "fail"
    ][:12]

    return {
        "portal": portal,
        "actor": meta.get("actor"),
        "status": overall,
        "query_count": len(query_reports),
        "pass_count": status_counts.get("pass", 0),
        "warn_count": status_counts.get("warn", 0),
        "fail_count": status_counts.get("fail", 0),
        "raw_total": raw_total,
        "kept_total": kept_total,
        "keep_rate": _rate(kept_total, raw_total),
        "failed_queries": failed_queries,
    }


def summarize_all_portals(sources_meta: dict[str, Any]) -> dict[str, Any]:
    """Cross-portal validation summary for market_manifest.json."""
    portals = []
    for source_name, meta in sources_meta.items():
        if not isinstance(meta, dict):
            continue
        if source_name in {"easybroker"}:
            continue
        summary = summarize_portal({**meta, "source": meta.get("source") or source_name})
        portals.append(summary)

    overall = "pass"
    if any(p.get("status") == "fail" for p in portals):
        overall = "fail"
    elif any(p.get("status") == "warn" for p in portals):
        overall = "warn"

    colonia_compare = compare_colonia_queries(sources_meta)

    return {
        "status": overall,
        "portals": portals,
        "colonia_compare": colonia_compare,
    }


def compare_colonia_queries(sources_meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Side-by-side kept counts for colonia-scoped queries across portals."""
    by_key: dict[str, dict[str, Any]] = {}

    for source_name, meta in sources_meta.items():
        if not isinstance(meta, dict):
            continue
        portal = str(meta.get("source") or source_name)
        for q in meta.get("queries") or []:
            if not isinstance(q, dict):
                continue
            if str(q.get("scope") or "").lower() != "colonia":
                continue
            key = query_key(q)
            bucket = by_key.setdefault(
                key,
                {
                    "query_key": key,
                    "colonia": q.get("colonia"),
                    "city": q.get("city"),
                    "propertyType": q.get("propertyType"),
                    "transactionType": q.get("transactionType"),
                    "portals": {},
                },
            )
            validation = q.get("validation") if isinstance(q.get("validation"), dict) else {}
            bucket["portals"][portal] = {
                "kept_count": q.get("count", 0),
                "raw_count": q.get("raw_count", 0),
                "status": validation.get("status"),
                "search_url": q.get("search_url") or validation.get("search_url"),
            }

    rows = list(by_key.values())
    rows.sort(key=lambda row: str(row.get("colonia") or row.get("query_key")))
    return rows


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
