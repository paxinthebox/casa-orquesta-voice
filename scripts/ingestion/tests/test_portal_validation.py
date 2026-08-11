"""Tests for portal ingest validation heuristics."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS))

from ingestion.portal_validation import validate_query_batch  # noqa: E402


def test_broad_serp_low_raw_geo_passes_when_rows_kept():
    """Inmuebles24 often returns national junk; warn only when nothing survives sanitize."""
    query = {
        "city": "Emiliano Zapata",
        "state": "Morelos",
        "propertyType": "casas",
        "transactionType": "renta",
    }
    raw_rows = [
        {
            "posting_id": str(i),
            "url": f"https://www.inmuebles24.com/propiedades/{100000 + i}.html",
            "title": "Casa en Guadalajara, Jalisco",
        }
        for i in range(76)
    ] + [
        {
            "posting_id": str(i),
            "url": f"https://www.inmuebles24.com/propiedades/{200000 + i}.html",
            "title": f"Casa en renta Emiliano Zapata, Morelos #{i}",
            "postingLocation_location_parent_name": "Emiliano Zapata",
            "postingLocation_location_name": "Centro",
        }
        for i in range(4)
    ]
    kept = [
        {
            "title": "Casa en Emiliano Zapata",
            "city": "Emiliano Zapata",
            "state": "Morelos",
            "price_mxn": 12000,
        }
        for _ in range(4)
    ]

    def _normalize(raw, q=None):
        title = str(raw.get("title") or "")
        if "emiliano zapata" in title.lower():
            return kept[0]
        return None

    report = validate_query_batch(
        portal="inmuebles24",
        query=query,
        raw_rows=raw_rows,
        kept_rows=kept,
        normalize_fn=_normalize,
    )
    assert report["status"] == "pass"
    assert report["kept_count"] == 4
    assert report["raw_in_market_rate"] is not None
    assert report["raw_in_market_rate"] < 0.3


def test_broad_serp_low_raw_geo_warns_when_nothing_kept():
    query = {
        "city": "Cuernavaca",
        "state": "Morelos",
        "propertyType": "casas",
        "transactionType": "renta",
    }
    raw_rows = [
        {
            "posting_id": str(i),
            "title": "Casa en Monterrey, Nuevo León",
        }
        for i in range(80)
    ]

    def _reject(_raw, _q=None):
        return None

    report = validate_query_batch(
        portal="inmuebles24",
        query=query,
        raw_rows=raw_rows,
        kept_rows=[],
        normalize_fn=_reject,
    )
    assert report["status"] == "warn"
    assert any("CDMX/Morelos pilot" in issue for issue in report["issues"])
