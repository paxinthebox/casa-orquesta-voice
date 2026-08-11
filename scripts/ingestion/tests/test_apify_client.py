"""Tests for Apify client error formatting."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.apify_client import (  # noqa: E402
    SYNC_RUN_MAX_SEC,
    attribute_rows_to_queries,
    canonicalize_search_url,
    chunk_queries,
    fatal_apify_actor_error,
    format_request_error,
    merge_batch_run_input,
    portal_batch_size,
    portal_max_items,
    prefer_async_run,
)


def test_format_timeout_error_when_message_empty():
    err = format_request_error(httpx.ReadTimeout(""), timeout_sec=300)
    assert "timed out" in err
    assert "300" in err


def test_format_http_error_preserves_detail():
    err = format_request_error(httpx.ConnectError("connection refused"), timeout_sec=300)
    assert "connection refused" in err


def test_prefer_async_when_timeout_above_sync_cap(monkeypatch):
    monkeypatch.delenv("APIFY_USE_ASYNC_RUNS", raising=False)
    assert prefer_async_run(SYNC_RUN_MAX_SEC + 1) is True
    assert prefer_async_run(SYNC_RUN_MAX_SEC) is False


def test_portal_max_items_respects_per_portal_cap(monkeypatch):
    monkeypatch.setenv("MARKET_MAX_ITEMS_PER_QUERY", "80")
    monkeypatch.setenv("APIFY_PROPIEDADES_MAX_ITEMS", "60")
    assert portal_max_items("propiedades") == 60


def test_portal_max_items_falls_back_to_global_ceiling(monkeypatch):
    monkeypatch.setenv("MARKET_MAX_ITEMS_PER_QUERY", "80")
    monkeypatch.delenv("APIFY_INMUEBLES24_MAX_ITEMS", raising=False)
    assert portal_max_items("inmuebles24") == 80


def test_fatal_apify_actor_error_detects_not_rented():
    msg = 'HTTP 403: {"error":{"type":"actor-is-not-rented","message":"rent me"}}'
    reason = fatal_apify_actor_error(msg)
    assert reason is not None
    assert "not rented" in reason.lower()


def test_fatal_apify_actor_error_ignores_transient():
    assert fatal_apify_actor_error("HTTP 429: rate limit") is None


def test_chunk_queries_respects_batch_size():
    queries = [{"id": i} for i in range(5)]
    assert len(chunk_queries(queries, 1)) == 5
    assert len(chunk_queries(queries, 2)) == 3
    assert chunk_queries(queries, 15) == [queries]


def test_canonicalize_search_url_vivanuncios_page():
    a = "https://www.vivanuncios.com.mx/s-casas-en-venta/x/v1c1293l13521p3"
    b = "https://www.vivanuncios.com.mx/s-casas-en-venta/x/v1c1293l13521p1"
    assert canonicalize_search_url(a) == canonicalize_search_url(b)


def test_merge_batch_run_input_start_urls():
    def build(query, max_items, proxy):
        return {
            "startUrl": f"https://www.lamudi.com.mx/{query['slug']}/",
            "maxItems": max_items,
        }

    queries = [{"slug": "a"}, {"slug": "b"}]
    merged = merge_batch_run_input(
        queries,
        max_items=40,
        proxy=None,
        build_run_input=build,
        search_url_for_query=lambda q: f"https://www.lamudi.com.mx/{q['slug']}/",
    )
    assert "startUrl" not in merged
    assert merged["startUrls"] == [
        "https://www.lamudi.com.mx/a/",
        "https://www.lamudi.com.mx/b/",
    ]
    assert merged["maxItemsPerUrl"] == 40
    assert merged["maxItems"] == 80


def test_merge_batch_run_input_store_urls():
    def build(query, max_items, proxy):
        return {
            "urls": [f"https://www.vivanuncios.com.mx/{query['slug']}/"],
            "max_items_per_url": max_items,
            "ignore_url_failures": True,
        }

    queries = [{"slug": "roma"}, {"slug": "condesa"}]
    merged = merge_batch_run_input(
        queries,
        max_items=20,
        proxy=None,
        build_run_input=build,
    )
    assert merged["urls"] == [
        "https://www.vivanuncios.com.mx/roma/",
        "https://www.vivanuncios.com.mx/condesa/",
    ]


def test_attribute_rows_to_queries_by_query_url():
    queries = [
        {"colonia": "Roma"},
        {"colonia": "Condesa"},
    ]
    urls = [
        "https://www.vivanuncios.com.mx/s-casas-en-venta/roma/v1c1293l13669p1",
        "https://www.vivanuncios.com.mx/s-casas-en-venta/condesa/v1c1293l13636p1",
    ]

    def search_url(q):
        return urls[0] if q["colonia"] == "Roma" else urls[1]

    raw_dicts = [
        {"posting_id": "1", "query_url": urls[1], "url": "/d-x/1"},
        {"posting_id": "2", "query_url": urls[0], "url": "/d-x/2"},
    ]

    def flatten(raw):
        return dict(raw)

    def normalize(raw, query):
        return {"id": f"VA-{raw['posting_id']}", "colonia": query["colonia"]}

    buckets = attribute_rows_to_queries(
        raw_dicts,
        queries,
        flatten=flatten,
        normalize_row=normalize,
        search_url_for_query=search_url,
    )
    assert len(buckets[0][1]) == 1
    assert buckets[0][1][0]["colonia"] == "Roma"
    assert buckets[1][1][0]["colonia"] == "Condesa"


def test_portal_batch_size_default_and_override(monkeypatch):
    monkeypatch.delenv("APIFY_URL_BATCH_SIZE", raising=False)
    monkeypatch.delenv("APIFY_VIVANUNCIOS_BATCH_SIZE", raising=False)
    assert portal_batch_size("vivanuncios", default=15) == 15
    monkeypatch.setenv("APIFY_VIVANUNCIOS_BATCH_SIZE", "20")
    assert portal_batch_size("vivanuncios", default=15) == 20
