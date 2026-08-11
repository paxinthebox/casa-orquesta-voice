"""Orchestrate whole-market ingestion → listings service + people index."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_SHARED = Path(__file__).resolve().parents[2] / "services" / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

from financing import enrich_listings_financing  # noqa: E402

from .dedupe import dedupe_listings
from .contact_extract import enrich_listings_contacts
from .easybroker import EasyBrokerClient, EasyBrokerError
from .people_index import build_market_people_index
from .portal_validation import summarize_all_portals
from .portals.inmuebles24 import fetch_inmuebles24_market, inmuebles24_enabled, load_market_queries
from .portals.lamudi import fetch_lamudi_market, lamudi_enabled
from .portals.mercadolibre import fetch_mercadolibre_market, mercadolibre_enabled
from .portals.propiedades import fetch_propiedades_market, propiedades_enabled
from .portals.vivanuncios import fetch_vivanuncios_market, vivanuncios_enabled
from .queries import filter_queries, load_colonia_queries
from .portal_sanitize import sanitize_portal_batch


def _ingest_log(msg: str) -> None:
    print(f"[ingest-market] {msg}", flush=True)


def _listings_url() -> str:
    return os.getenv("LISTINGS_URL", "http://localhost:8002").rstrip("/")


_PUSH_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TRANSIENT_HTTP_ERRORS = (
    httpx.ReadError,
    httpx.ConnectError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)
_PUSH_EARLY_ABORT = 15


async def _verify_listings_api() -> None:
    """Fail fast when the catalog API is down (health alone does not touch Postgres)."""
    url = _listings_url()
    async with httpx.AsyncClient(timeout=_PUSH_TIMEOUT) as client:
        try:
            health = await client.get(f"{url}/health")
            health.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"El servicio de listings no responde en {url} — ¿corriste make dev? ({exc!r})"
            ) from exc
        try:
            probe = await client.post(
                f"{url}/listings",
                json={
                    "id": "__ingest_probe__",
                    "data": {
                        "id": "__ingest_probe__",
                        "state": "CDMX",
                        "type": "departamento",
                        "price_mxn": 1,
                        "status": "available",
                        "source": "ingest_probe",
                    },
                },
            )
            probe.raise_for_status()
            await client.delete(f"{url}/listings/__ingest_probe__")
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Listings en {url} responde /health pero no acepta POST /listings "
                f"(¿Postgres caído?). Detalle: {exc!r}"
            ) from exc


async def _post_one_listing(
    client: httpx.AsyncClient,
    lid: str,
    payload: dict[str, Any],
) -> None:
    last_exc: httpx.HTTPError | None = None
    for attempt in range(3):
        try:
            resp = await client.post(
                f"{_listings_url()}/listings",
                json={"id": lid, "data": payload},
            )
            resp.raise_for_status()
            return
        except _TRANSIENT_HTTP_ERRORS as exc:
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(0.4 * (attempt + 1))
                continue
            raise
        except httpx.HTTPStatusError:
            raise
    if last_exc is not None:
        raise last_exc


def _people_index_path() -> Path:
    return Path(os.getenv("PEOPLE_INDEX_PATH", "data/cache/people_index.json"))


def _manifest_path() -> Path:
    return Path(os.getenv("MARKET_MANIFEST_PATH", "data/cache/market_manifest.json"))


def _listings_cache_path() -> Path:
    return Path(os.getenv("MARKET_LISTINGS_CACHE", "data/cache/market_listings.json"))


async def purge_stale_portal_listings(valid_ids: set[str]) -> int:
    """Remove old portal rows whose IDs changed after re-sanitize."""
    removed = 0
    portal_prefixes = ("I24-", "VA-", "PROP-", "LAM-", "ML-")
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(f"{_listings_url()}/listings", params={"limit": 500})
            resp.raise_for_status()
            rows = resp.json()
        except httpx.HTTPError:
            return 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            lid = str(row.get("id") or "")
            if not any(lid.startswith(prefix) for prefix in portal_prefixes):
                continue
            if lid in valid_ids:
                continue
            try:
                del_resp = await client.delete(f"{_listings_url()}/listings/{lid}")
                if del_resp.status_code == 200:
                    removed += 1
            except httpx.HTTPError:
                continue
    return removed


async def push_listings(listings: list[dict[str, Any]]) -> dict[str, int]:
    await _verify_listings_api()
    valid_ids = {str(row.get("id")) for row in listings if row.get("id")}
    removed = await purge_stale_portal_listings(valid_ids)
    if removed:
        _ingest_log(f"Purged {removed} stale portal listing(s) from API")
    total = len(listings)
    _ingest_log(f"Pushing {total} listings to {_listings_url()} …")
    ok = 0
    failed = 0
    first_error: str | None = None
    aborted = False
    client = httpx.AsyncClient(timeout=_PUSH_TIMEOUT)
    try:
        for i, row in enumerate(listings, start=1):
            lid = row.get("id")
            if not lid:
                failed += 1
                continue
            payload = {k: v for k, v in row.items() if not str(k).startswith("_")}
            try:
                await _post_one_listing(client, str(lid), payload)
                ok += 1
            except httpx.HTTPError as exc:
                failed += 1
                if first_error is None:
                    detail = ""
                    if isinstance(exc, httpx.HTTPStatusError):
                        detail = exc.response.text[:160]
                    first_error = f"{exc!r} {detail}".strip()
                if ok == 0 and failed >= _PUSH_EARLY_ABORT:
                    _ingest_log(
                        f"Aborting push after {failed} fallos seguidos — "
                        f"revisa make dev y LISTINGS_URL={_listings_url()}"
                    )
                    aborted = True
                    break
                if isinstance(exc, _TRANSIENT_HTTP_ERRORS):
                    await client.aclose()
                    client = httpx.AsyncClient(timeout=_PUSH_TIMEOUT)
            if i % 25 == 0 or i == total:
                _ingest_log(f"Push progress: {i}/{total} ({ok} ok, {failed} failed)")
    finally:
        await client.aclose()
    out: dict[str, Any] = {"ok": ok, "failed": failed, "purged": removed}
    if aborted:
        out["aborted"] = True
        out["attempted"] = ok + failed
    if first_error:
        out["first_error"] = first_error
    return out


def write_listings_cache(listings: list[dict[str, Any]]) -> Path:
    path = _listings_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(listings, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_listings_cache(*, repair: bool = True) -> list[dict[str, Any]]:
    path = _listings_cache_path()
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    rows = [row for row in raw if isinstance(row, dict)]
    if not repair:
        return rows
    queries = load_market_queries()
    from .contact_extract import enrich_listings_contacts

    return enrich_listings_financing(
        enrich_listings_contacts(sanitize_portal_batch(rows, queries=queries))
    )


async def push_cached_listings() -> dict[str, Any]:
    listings = load_listings_cache()
    if not listings:
        raise RuntimeError(
            f"No hay caché en {_listings_cache_path()}. "
            "Corre make ingest-market primero (sin --no-push)."
        )
    push = await push_listings(listings)
    return {"listings_push": push, "cached_count": len(listings)}


def write_people_index(
    collaborators: list[dict[str, Any]],
    brokers: list[dict[str, Any]],
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    path = _people_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "collaborators": collaborators,
        "brokers": brokers,
        "meta": meta or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_manifest(payload: dict[str, Any]) -> Path:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def _fetch_easybroker(
    *,
    include_mls: bool,
    max_pages: int,
) -> dict[str, Any]:
    api_key = os.getenv("EASYBROKER_API_KEY", "").strip()
    if not api_key:
        return {
            "listings": [],
            "collaborators": [],
            "brokers": [],
            "meta": {"skipped": True, "reason": "EASYBROKER_API_KEY not set"},
        }
    client = EasyBrokerClient(api_key)
    return await client.ingest_all(include_mls=include_mls, max_pages=max_pages)


def merge_listings_cache(
    fresh: list[dict[str, Any]],
    *,
    repair: bool = True,
) -> list[dict[str, Any]]:
    """Union fresh scrape rows into the on-disk cache (by listing id)."""
    existing = load_listings_cache(repair=repair)
    by_id: dict[str, dict[str, Any]] = {}
    for row in existing:
        lid = str(row.get("id") or "")
        if lid:
            by_id[lid] = row
    for row in fresh:
        lid = str(row.get("id") or "")
        if lid:
            by_id[lid] = row
    return list(by_id.values())


async def run_market_ingest(
    *,
    include_portal: bool = True,
    include_easybroker: bool = True,
    include_mls: bool = True,
    max_pages: int = 10,
    push: bool = True,
    queries_path: Path | None = None,
    merge_cache: bool = False,
    colonia_pilot: bool = False,
    colonia_names: set[str] | None = None,
) -> dict[str, Any]:
    """Merge portal + EasyBroker listings, dedupe, build people index, push to listings."""
    if queries_path:
        os.environ["MARKET_QUERIES_PATH"] = str(queries_path)

    queries = load_market_queries()
    if colonia_pilot:
        queries = load_colonia_queries()
    queries = filter_queries(
        queries,
        colonia_only=colonia_pilot,
        colonia_names=colonia_names,
    )

    sources_meta: dict[str, Any] = {}
    combined: list[dict[str, Any]] = []
    eb_collaborators: list[dict[str, Any]] = []
    eb_brokers: list[dict[str, Any]] = []

    _ingest_log("Starting whole-market ingest …")
    if colonia_pilot:
        _ingest_log(f"Colonia pilot mode — {len(queries)} colonia queries")

    if include_portal:
        if inmuebles24_enabled():
            portal_rows, portal_meta = await fetch_inmuebles24_market(queries=queries)
            combined.extend(portal_rows)
            sources_meta["inmuebles24"] = portal_meta
        else:
            _ingest_log("Inmuebles24 skipped (INGEST_PORTALS / INGEST_INMUEBLES24)")

        if vivanuncios_enabled():
            va_rows, va_meta = await fetch_vivanuncios_market(queries=queries)
            combined.extend(va_rows)
            sources_meta["vivanuncios"] = va_meta
        else:
            _ingest_log("Vivanuncios skipped (INGEST_PORTALS / INGEST_VIVANUNCIOS)")

        if propiedades_enabled():
            prop_rows, prop_meta = await fetch_propiedades_market(queries=queries)
            combined.extend(prop_rows)
            sources_meta["propiedades"] = prop_meta
        else:
            _ingest_log("Propiedades skipped (INGEST_PORTALS / INGEST_PROPIEDADES)")

        if lamudi_enabled():
            lam_rows, lam_meta = await fetch_lamudi_market(queries=queries)
            combined.extend(lam_rows)
            sources_meta["lamudi"] = lam_meta
        else:
            _ingest_log("Lamudi skipped (INGEST_PORTALS / INGEST_LAMUDI)")

        if mercadolibre_enabled():
            ml_rows, ml_meta = await fetch_mercadolibre_market(queries=queries)
            combined.extend(ml_rows)
            sources_meta["mercadolibre"] = ml_meta
        else:
            _ingest_log("MercadoLibre skipped (INGEST_PORTALS / INGEST_MERCADOLIBRE)")
    else:
        _ingest_log("Portal scrape skipped (--no-portal)")

    if include_easybroker:
        _ingest_log("EasyBroker: fetching inventory …")
        try:
            eb = await _fetch_easybroker(include_mls=include_mls, max_pages=max_pages)
        except EasyBrokerError as exc:
            _ingest_log(f"EasyBroker: error — {exc}")
            eb = {
                "listings": [],
                "collaborators": [],
                "brokers": [],
                "meta": {"error": str(exc)},
            }
        else:
            eb_meta = eb.get("meta") or {}
            _ingest_log(
                f"EasyBroker: {eb_meta.get('listing_count', 0)} published listings, "
                f"{eb_meta.get('collaborator_count', 0)} collaborators, "
                f"{eb_meta.get('broker_count', 0)} brokers"
            )
        combined.extend(eb.get("listings") or [])
        eb_collaborators = list(eb.get("collaborators") or [])
        eb_brokers = list(eb.get("brokers") or [])
        sources_meta["easybroker"] = eb.get("meta") or {}
    else:
        _ingest_log("EasyBroker skipped (--no-easybroker)")

    before = len(combined)
    _ingest_log(f"Deduplicating {before} raw listings …")
    listings = dedupe_listings(combined)
    listings = sanitize_portal_batch(listings, queries=queries)
    listings = enrich_listings_contacts(listings)
    listings = enrich_listings_financing(listings)
    after = len(listings)
    _ingest_log(f"Dedupe complete: {after} listings ({before - after} removed)")

    if merge_cache:
        prev_n = len(load_listings_cache(repair=False))
        listings = merge_listings_cache(listings, repair=False)
        _ingest_log(
            f"Merged into cache: {after} new/updated + {prev_n} existing → {len(listings)} total",
        )
        after = len(listings)

    collaborators, brokers = build_market_people_index(
        listings,
        eb_collaborators=eb_collaborators,
        eb_brokers=eb_brokers,
    )

    manifest = {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "sources": sources_meta,
        "portal_validation": summarize_all_portals(sources_meta),
        "listings_before_dedupe": before,
        "listings_after_dedupe": after,
        "collaborator_count": len(collaborators),
        "broker_count": len(brokers),
    }

    result: dict[str, Any] = {
        "meta": manifest,
        "people_index": None,
        "manifest_path": None,
        "listings_push": None,
        "listings_cache": None,
    }

    cache_path = write_listings_cache(listings)
    result["listings_cache"] = str(cache_path.resolve())
    _ingest_log(f"Wrote listings cache → {cache_path.resolve()}")

    if push and listings:
        result["listings_push"] = await push_listings(listings)
        manifest["listings_push"] = result["listings_push"]
    elif push:
        _ingest_log("Push skipped — no listings to upload")

    index_path = write_people_index(
        collaborators,
        brokers,
        meta=manifest,
    )
    manifest_path = write_manifest(manifest)

    result["people_index"] = str(index_path.resolve())
    result["manifest_path"] = str(manifest_path.resolve())
    return result


async def run_ingest(
    *,
    include_mls: bool = True,
    max_pages: int = 10,
    push: bool = True,
) -> dict[str, Any]:
    """Backward-compatible EasyBroker-only ingest."""
    api_key = os.getenv("EASYBROKER_API_KEY", "").strip()
    if not api_key:
        raise EasyBrokerError(
            "Configura EASYBROKER_API_KEY en .env — "
            "la obtienes en EasyBroker → Configuración → API para programadores."
        )
    return await run_market_ingest(
        include_portal=False,
        include_easybroker=True,
        include_mls=include_mls,
        max_pages=max_pages,
        push=push,
    )
