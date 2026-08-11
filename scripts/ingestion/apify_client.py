"""Shared Apify client for market portal adapters."""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from typing import Any

import httpx

from .portal_validation import validate_query_batch

# Apify sync endpoint hard-caps HTTP wait at 300s regardless of ?timeout= value.
SYNC_RUN_MAX_SEC = 300


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def proxy_configuration() -> dict[str, Any] | None:
    flag = os.getenv("APIFY_USE_RESIDENTIAL_PROXY", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return None
    return {
        "useApifyProxy": True,
        "apifyProxyGroups": ["RESIDENTIAL"],
        "apifyProxyCountry": "MX",
    }


def apify_status_error(rows: list[Any]) -> str | None:
    """Apify actors sometimes return [{\"message\": \"Rate limit…\"}] instead of listings."""
    if not rows:
        return None
    messages: list[str] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        msg = str(raw.get("message") or "").strip()
        if not msg:
            continue
        if raw.get("url") or raw.get("link") or raw.get("detailUrl") or raw.get("postingId"):
            continue
        if raw.get("posting_id") or raw.get("title"):
            continue
        messages.append(msg)
    if not messages:
        return None
    if len(messages) == len(rows):
        return messages[0]
    return None


def actor_path(actor_slug: str) -> str:
    return actor_slug.replace("/", "~")


def apify_build_tag(portal: str) -> str | None:
    """Optional build override (e.g. APIFY_LAMUDI_BUILD=0.2.1 when latest tag missing)."""
    raw = os.getenv(f"APIFY_{portal.upper()}_BUILD", "").strip()
    return raw or None


def portal_max_items(portal: str, override: int | None = None) -> int:
    """
    Per-portal raw row cap before geo sanitize.

    APIFY_{PORTAL}_MAX_ITEMS overrides the global MARKET_MAX_ITEMS_PER_QUERY ceiling.
    """
    ceiling = env_int("MARKET_MAX_ITEMS_PER_QUERY", 80)
    if override is not None and override > 0:
        return min(override, ceiling)
    portal_cap = env_int(f"APIFY_{portal.upper()}_MAX_ITEMS", 0)
    if portal_cap > 0:
        return min(portal_cap, ceiling)
    return ceiling


def portal_batch_size(portal: str, *, default: int = 0) -> int:
    """
    How many search URLs to send per Apify run (full-grid multi-URL mode).

    APIFY_{PORTAL}_BATCH_SIZE overrides APIFY_URL_BATCH_SIZE.
    0 or 1 = one query per run (legacy).
    Pass ``default=15`` from URL-batch portals (VA / Lamudi live / ML live).
    """
    per = env_int(f"APIFY_{portal.upper()}_BATCH_SIZE", 0)
    if per > 0:
        return per
    global_batch = env_int("APIFY_URL_BATCH_SIZE", 0)
    if global_batch > 0:
        return global_batch
    return max(0, default)


def canonicalize_search_url(url: str) -> str:
    """Normalize SERP URLs so query_url tags match search_url_for_query()."""
    cleaned = str(url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    # Vivanuncios page suffix: …/v1c1293l13521p2 → …p1
    if "vivanuncios.com" in cleaned:
        import re

        cleaned = re.sub(r"p\d+$", "p1", cleaned)
    # Lamudi ?page= already stripped via split
    # Mercado Libre _Desde_N pagination
    if "mercadolibre.com" in cleaned:
        import re

        cleaned = re.sub(r"/_Desde_\d+/?$", "/", cleaned).rstrip("/")
    return cleaned.lower()


def chunk_queries(
    queries: list[dict[str, Any]],
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    if batch_size <= 1:
        return [[q] for q in queries]
    return [queries[i : i + batch_size] for i in range(0, len(queries), batch_size)]


def merge_batch_run_input(
    queries: list[dict[str, Any]],
    *,
    max_items: int,
    proxy: dict[str, Any] | None,
    build_run_input: Callable[[dict[str, Any], int, dict[str, Any] | None], dict[str, Any]],
    search_url_for_query: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """
    Build one Apify input covering many market queries.

    Prefers ``startUrls`` (custom live actors). Falls back to ``urls`` (store actors
    like stealth_mode/vivanuncios).
    """
    if not queries:
        return {}

    def _http_url(value: str) -> str:
        cleaned = str(value or "").strip()
        return cleaned if cleaned.startswith("http") else ""

    urls: list[str] = []
    for query in queries:
        hint = ""
        if search_url_for_query:
            hint = _http_url(search_url_for_query(query))
        single = build_run_input(query, max_items, proxy)
        url = (
            hint
            or _http_url(str(single.get("startUrl") or ""))
            or (
                _http_url(str((single.get("urls") or [""])[0]))
                if isinstance(single.get("urls"), list) and single.get("urls")
                else ""
            )
        )
        if url:
            urls.append(url)

    base = build_run_input(queries[0], max_items, proxy)
    base.pop("startUrl", None)
    # Store actors (VA / I24 / Propiedades / ML legacy) use ``urls``.
    if "urls" in base:
        base["urls"] = urls or base.get("urls") or []
        base["max_items_per_url"] = max_items
        base.setdefault("ignore_url_failures", True)
        return base

    # Custom live actors use ``startUrls`` (+ optional legacy ``startUrl``).
    base["startUrls"] = urls
    base["maxItemsPerUrl"] = max_items
    if "maxItems" in base:
        # Cap total dataset size; actors also enforce per-URL caps.
        base["maxItems"] = max_items * max(1, len(urls))
    return base


def attribute_rows_to_queries(
    raw_dicts: list[dict[str, Any]],
    queries: list[dict[str, Any]],
    *,
    flatten: Callable[[dict[str, Any]], dict[str, Any]],
    normalize_row: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any] | None],
    search_url_for_query: Callable[[dict[str, Any]], str] | None,
) -> dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
    """
    Split a multi-URL Apify dataset back into per-query (raw, kept) buckets.

    Prefer ``query_url`` / ``search_url`` tags from custom actors; fall back to
    trying normalize_row against each query in the batch.
    """
    url_to_idx: dict[str, int] = {}
    for idx, query in enumerate(queries):
        if not search_url_for_query:
            continue
        key = canonicalize_search_url(search_url_for_query(query))
        if key:
            url_to_idx[key] = idx

    buckets: dict[int, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {
        i: ([], []) for i in range(len(queries))
    }

    for raw in raw_dicts:
        flat = flatten(raw)
        tagged = canonicalize_search_url(
            str(flat.get("query_url") or flat.get("search_url") or raw.get("query_url") or "")
        )
        idx: int | None = url_to_idx.get(tagged) if tagged else None
        if idx is None:
            for candidate_i, query in enumerate(queries):
                if normalize_row(raw, query):
                    idx = candidate_i
                    break
        if idx is None:
            idx = 0
        raw_list, kept_list = buckets[idx]
        raw_list.append(raw)
        item = normalize_row(raw, queries[idx])
        if item:
            kept_list.append(item)
        buckets[idx] = (raw_list, kept_list)

    return buckets


def _build_query_params(
    *,
    token: str,
    timeout: int,
    build: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"token": token, "timeout": timeout}
    if build:
        params["build"] = build
    return params


def prefer_async_run(run_timeout: int) -> bool:
    """Use start-run + poll when sync API's 300s cap is too tight."""
    flag = os.getenv("APIFY_USE_ASYNC_RUNS", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return run_timeout > SYNC_RUN_MAX_SEC


def format_request_error(exc: BaseException, *, timeout_sec: int) -> str:
    """Human-readable Apify/HTTP client error (httpx timeouts often have empty str())."""
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Apify run timed out after ~{timeout_sec}s "
            "(scraper still paginating or Apify queue overloaded)"
        )
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        body = exc.response.text[:240].strip()
        if body:
            return f"HTTP {exc.response.status_code}: {body}"
    text = str(exc).strip()
    if text:
        return text
    return f"{type(exc).__name__} (no detail from HTTP client)"


def fatal_apify_actor_error(message: str) -> str | None:
    """Return a short abort reason when remaining portal queries cannot succeed."""
    msg = message.lower()
    if "actor-is-not-rented" in msg:
        return (
            "Apify actor not rented (free trial ended — rent on console.apify.com "
            "or remove mercadolibre from INGEST_PORTALS)"
        )
    if "actor-not-found" in msg:
        return "Apify actor not found (check APIFY_*_ACTOR slug)"
    if "apify-plan-required" in msg:
        return "Apify plan upgrade required for this actor"
    return None


def _retryable_http_status(code: int) -> bool:
    return code in {408, 429, 502, 503, 504}


def _sync_timeout(run_timeout: int) -> int:
    return min(run_timeout, SYNC_RUN_MAX_SEC)


async def _start_actor_run(
    client: httpx.AsyncClient,
    *,
    actor: str,
    token: str,
    run_input: dict[str, Any],
    run_timeout: int,
    build: str | None = None,
) -> dict[str, Any]:
    url = f"https://api.apify.com/v2/acts/{actor}/runs"
    resp = await client.post(
        url,
        params=_build_query_params(token=token, timeout=run_timeout, build=build),
        json=run_input,
    )
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}: {resp.text[:160]}",
            request=resp.request,
            response=resp,
        )
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise RuntimeError("unexpected Apify run response shape")
    return data


async def _poll_actor_run(
    client: httpx.AsyncClient,
    *,
    run_id: str,
    token: str,
    deadline: float,
) -> dict[str, Any]:
    url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    poll_interval = max(2, env_int("APIFY_POLL_INTERVAL_SEC", 5))
    while time.monotonic() < deadline:
        resp = await client.get(url, params={"token": token})
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}: {resp.text[:160]}",
                request=resp.request,
                response=resp,
            )
        payload = resp.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("unexpected Apify run status response")
        status = str(data.get("status") or "")
        if status == "SUCCEEDED":
            return data
        if status in {"FAILED", "TIMED-OUT", "ABORTED"}:
            msg = str(
                data.get("statusMessage")
                or data.get("exitCode")
                or status
            )
            raise RuntimeError(f"Apify run {status.lower()}: {msg}")
        await asyncio.sleep(poll_interval)
    raise httpx.ReadTimeout(f"Apify run {run_id} did not finish before deadline")


async def _fetch_dataset_items(
    client: httpx.AsyncClient,
    *,
    dataset_id: str,
    token: str,
) -> list[Any]:
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    resp = await client.get(
        url,
        params={"token": token, "format": "json", "clean": "1"},
    )
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}: {resp.text[:160]}",
            request=resp.request,
            response=resp,
        )
    rows = resp.json()
    return rows if isinstance(rows, list) else []


async def _run_actor_async(
    client: httpx.AsyncClient,
    *,
    actor: str,
    token: str,
    run_input: dict[str, Any],
    run_timeout: int,
    build: str | None = None,
) -> list[Any]:
    run = await _start_actor_run(
        client,
        actor=actor,
        token=token,
        run_input=run_input,
        run_timeout=run_timeout,
        build=build,
    )
    run_id = str(run.get("id") or "")
    dataset_id = str(run.get("defaultDatasetId") or "")
    if not run_id:
        raise RuntimeError("Apify run start did not return run id")
    deadline = time.monotonic() + float(run_timeout) + 45.0
    finished = await _poll_actor_run(
        client,
        run_id=run_id,
        token=token,
        deadline=deadline,
    )
    dataset_id = str(finished.get("defaultDatasetId") or dataset_id)
    if not dataset_id:
        raise RuntimeError(f"Apify run {run_id} succeeded without dataset id")
    return await _fetch_dataset_items(client, dataset_id=dataset_id, token=token)


async def _run_actor_sync(
    client: httpx.AsyncClient,
    *,
    actor: str,
    token: str,
    run_input: dict[str, Any],
    run_timeout: int,
    build: str | None = None,
) -> list[Any]:
    sync_timeout = _sync_timeout(run_timeout)
    params = _build_query_params(token=token, timeout=sync_timeout, build=build)
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?{query}"
    resp = await client.post(url, json=run_input)
    if resp.status_code >= 400:
        raise httpx.HTTPStatusError(
            f"HTTP {resp.status_code}: {resp.text[:160]}",
            request=resp.request,
            response=resp,
        )
    rows = resp.json()
    return rows if isinstance(rows, list) else []


async def _execute_apify_query(
    client: httpx.AsyncClient,
    *,
    actor: str,
    token: str,
    run_input: dict[str, Any],
    run_timeout: int,
    use_async: bool,
    build: str | None = None,
) -> list[Any]:
    if use_async:
        return await _run_actor_async(
            client,
            actor=actor,
            token=token,
            run_input=run_input,
            run_timeout=run_timeout,
            build=build,
        )
    return await _run_actor_sync(
        client,
        actor=actor,
        token=token,
        run_input=run_input,
        run_timeout=run_timeout,
        build=build,
    )


async def fetch_apify_portal(
    *,
    portal: str,
    actor_slug: str,
    queries: list[dict[str, Any]],
    build_run_input: Callable[[dict[str, Any], int, dict[str, Any] | None], dict[str, Any]],
    normalize_row: Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any] | None],
    flatten_row: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    search_url_for_query: Callable[[dict[str, Any]], str] | None = None,
    max_items: int | None = None,
    timeout_sec: int | None = None,
    log_prefix: str | None = None,
    batch_size: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run Apify actor over the market query grid; attach per-query validation.

    When ``batch_size`` (or APIFY_*_BATCH_SIZE) is > 1, many search URLs share one
    Apify run (full-grid multi-URL mode). Custom actors should tag each row with
    ``query_url`` matching the SERP start URL.
    """
    apify_token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not apify_token:
        return [], {"skipped": True, "reason": "APIFY_API_TOKEN not set"}

    prefix = log_prefix or portal
    limit = portal_max_items(portal, max_items)
    run_timeout = timeout_sec or env_int("APIFY_RUN_TIMEOUT_SEC", 300)
    max_attempts = max(1, env_int("APIFY_QUERY_RETRIES", 2))
    use_async = prefer_async_run(run_timeout)
    actor = actor_path(actor_slug)
    build = apify_build_tag(portal)
    flatten = flatten_row or (lambda row: row)
    proxy = proxy_configuration()
    client_timeout = float(run_timeout + 90 if use_async else _sync_timeout(run_timeout) + 60)
    resolved_batch = (
        batch_size if batch_size is not None else portal_batch_size(portal, default=0)
    )
    if resolved_batch < 1:
        resolved_batch = 1

    all_listings: list[dict[str, Any]] = []
    meta_queries: list[dict[str, Any]] = []
    total = len(queries)
    portal_abort: str | None = None
    batches = chunk_queries(queries, resolved_batch)

    mode = "async" if use_async else "sync"
    print(
        f"[ingest-market] {prefix} ({actor_slug}, {mode}): {total} queries in "
        f"{len(batches)} Apify run(s) "
        f"(batch={resolved_batch}, max {limit} items/url, up to {run_timeout}s)…",
        flush=True,
    )

    async with httpx.AsyncClient(timeout=client_timeout) as client:
        for batch_i, batch in enumerate(batches, start=1):
            labels = ", ".join(_query_label(q) for q in batch[:3])
            if len(batch) > 3:
                labels += f", …(+{len(batch) - 3})"
            search_hints = [
                search_url_for_query(q) if search_url_for_query else "" for q in batch
            ]

            if portal_abort:
                for query, hint in zip(batch, search_hints):
                    meta_queries.append({
                        **_query_meta_base(query, hint),
                        "error": portal_abort,
                        "skipped": True,
                        "count": 0,
                        "raw_count": 0,
                        "validation": validate_query_batch(
                            portal=portal,
                            query=query,
                            raw_rows=[],
                            kept_rows=[],
                            search_url=hint,
                            error=portal_abort,
                        ),
                    })
                continue

            print(
                f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}] "
                f"fetching {len(batch)} url(s): {labels}",
                flush=True,
            )
            t0 = time.monotonic()
            if len(batch) == 1:
                run_input = build_run_input(batch[0], limit, proxy)
            else:
                run_input = merge_batch_run_input(
                    batch,
                    max_items=limit,
                    proxy=proxy,
                    build_run_input=build_run_input,
                    search_url_for_query=search_url_for_query,
                )

            rows: list[Any] | None = None
            last_error = ""
            for attempt in range(1, max_attempts + 1):
                try:
                    rows = await _execute_apify_query(
                        client,
                        actor=actor,
                        token=apify_token,
                        run_input=run_input,
                        run_timeout=run_timeout,
                        use_async=use_async,
                        build=build,
                    )
                except httpx.HTTPError as exc:
                    last_error = format_request_error(exc, timeout_sec=run_timeout)
                    if attempt < max_attempts and isinstance(exc, httpx.TimeoutException):
                        print(
                            f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                            f"timeout, retry {attempt}/{max_attempts} …",
                            flush=True,
                        )
                        await asyncio.sleep(min(5 * attempt, 15))
                        continue
                    elapsed = time.monotonic() - t0
                    print(
                        f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                        f"error ({elapsed:.0f}s) — {last_error}",
                        flush=True,
                    )
                    for query, hint in zip(batch, search_hints):
                        meta_queries.append({
                            **_query_meta_base(query, hint),
                            "error": last_error,
                            "count": 0,
                            "raw_count": 0,
                            "validation": validate_query_batch(
                                portal=portal,
                                query=query,
                                raw_rows=[],
                                kept_rows=[],
                                search_url=hint,
                                error=last_error,
                            ),
                        })
                    fatal = fatal_apify_actor_error(last_error)
                    if fatal:
                        portal_abort = fatal
                        remaining = len(batches) - batch_i
                        if remaining:
                            print(
                                f"[ingest-market] {prefix}: {fatal} "
                                f"— skipping {remaining} remaining batches",
                                flush=True,
                            )
                    rows = None
                    break
                except RuntimeError as exc:
                    last_error = str(exc)
                    if attempt < max_attempts:
                        print(
                            f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                            f"{last_error} — retry {attempt}/{max_attempts}",
                            flush=True,
                        )
                        await asyncio.sleep(min(5 * attempt, 15))
                        continue
                    elapsed = time.monotonic() - t0
                    print(
                        f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                        f"error ({elapsed:.0f}s) — {last_error}",
                        flush=True,
                    )
                    for query, hint in zip(batch, search_hints):
                        meta_queries.append({
                            **_query_meta_base(query, hint),
                            "error": last_error,
                            "count": 0,
                            "raw_count": 0,
                            "validation": validate_query_batch(
                                portal=portal,
                                query=query,
                                raw_rows=[],
                                kept_rows=[],
                                search_url=hint,
                                error=last_error,
                            ),
                        })
                    fatal = fatal_apify_actor_error(last_error)
                    if fatal:
                        portal_abort = fatal
                    rows = None
                    break
                break

            if rows is None:
                continue

            if not isinstance(rows, list):
                elapsed = time.monotonic() - t0
                print(
                    f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                    f"unexpected response ({elapsed:.0f}s)",
                    flush=True,
                )
                for query, hint in zip(batch, search_hints):
                    meta_queries.append({
                        **_query_meta_base(query, hint),
                        "error": "unexpected response shape",
                        "count": 0,
                        "raw_count": 0,
                        "validation": validate_query_batch(
                            portal=portal,
                            query=query,
                            raw_rows=[],
                            kept_rows=[],
                            search_url=hint,
                            error="unexpected response shape",
                        ),
                    })
                continue

            status_err = apify_status_error(rows)
            if status_err:
                elapsed = time.monotonic() - t0
                print(
                    f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                    f"Apify status ({elapsed:.0f}s) — {status_err}",
                    flush=True,
                )
                raw_dicts_err = [r for r in rows if isinstance(r, dict)]
                for query, hint in zip(batch, search_hints):
                    meta_queries.append({
                        **_query_meta_base(query, hint),
                        "error": status_err,
                        "count": 0,
                        "raw_count": len(raw_dicts_err) if len(batch) == 1 else 0,
                        "validation": validate_query_batch(
                            portal=portal,
                            query=query,
                            raw_rows=raw_dicts_err if len(batch) == 1 else [],
                            kept_rows=[],
                            search_url=hint,
                            error=status_err,
                            flatten_fn=flatten,
                            normalize_fn=normalize_row,
                        ),
                    })
                continue

            raw_dicts = [raw for raw in rows if isinstance(raw, dict)]
            buckets = attribute_rows_to_queries(
                raw_dicts,
                batch,
                flatten=flatten,
                normalize_row=normalize_row,
                search_url_for_query=search_url_for_query,
            )
            elapsed = time.monotonic() - t0
            batch_kept = 0
            for qi, query in enumerate(batch):
                hint = search_hints[qi]
                raw_for_q, kept_for_q = buckets[qi]
                all_listings.extend(kept_for_q)
                batch_kept += len(kept_for_q)
                validation = validate_query_batch(
                    portal=portal,
                    query=query,
                    raw_rows=raw_for_q,
                    kept_rows=kept_for_q,
                    search_url=hint,
                    flatten_fn=flatten,
                    normalize_fn=normalize_row,
                )
                meta_queries.append({
                    **_query_meta_base(query, hint),
                    "count": len(kept_for_q),
                    "raw_count": len(raw_for_q),
                    "filtered_out": len(raw_for_q) - len(kept_for_q),
                    "error": None,
                    "validation": validation,
                })
                status = validation.get("status", "unknown")
                if len(batch) == 1 or len(kept_for_q) or validation.get("status") != "pass":
                    print(
                        f"[ingest-market] {prefix}   · {_query_label(query)}: "
                        f"{len(kept_for_q)} kept / {len(raw_for_q)} raw "
                        f"(validation={status})",
                        flush=True,
                    )
                    for issue in validation.get("issues") or []:
                        print(f"[ingest-market] {prefix}     ⚠ {issue}", flush=True)

            print(
                f"[ingest-market] {prefix} [batch {batch_i}/{len(batches)}]: "
                f"{batch_kept} listings kept ({elapsed:.0f}s, {len(raw_dicts)} raw)",
                flush=True,
            )

    print(f"[ingest-market] {prefix}: done — {len(all_listings)} listings total", flush=True)

    return all_listings, {
        "source": portal,
        "actor": actor_slug,
        "queries": meta_queries,
        "listing_count": len(all_listings),
        "batch_size": resolved_batch,
        "apify_runs": len(batches),
    }


def _query_label(query: dict[str, Any]) -> str:
    colonia = query.get("colonia")
    if colonia:
        return (
            f"{colonia} / {query.get('propertyType')} / {query.get('transactionType')}"
        )
    return (
        f"{query.get('city')} / {query.get('propertyType')} / "
        f"{query.get('transactionType')}"
    )


def _query_meta_base(query: dict[str, Any], search_url: str) -> dict[str, Any]:
    out = dict(query)
    if search_url:
        out["search_url"] = search_url
    return out
