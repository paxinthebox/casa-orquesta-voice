"""Tests for Lamudi live JSON-LD parser."""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

from ingestion.portals.lamudi_live_parse import (  # noqa: E402
    parse_jsonld_listings,
    parse_serp_html,
)

FIXTURE = ROOT / "data" / "fixtures" / "lamudi_prado_jsonld.json"


def test_parse_prado_fixture():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = parse_jsonld_listings(payload)
    assert len(rows) == 3
    first = rows[0]
    assert first["listing_id"].startswith("41032-73-")
    assert first["city"] == "Coyoacán"
    assert first["state"] == "CDMX"
    assert first["neighborhood"] == "Prado Churubusco"
    assert first["price"] == 3290000
    assert first["bedrooms"] == 2


def test_parse_serp_html_from_cache():
    html_path = ROOT / "data" / "cache" / "lamudi_prado_serp.html"
    if not html_path.is_file():
        return
    rows = parse_serp_html(
        html_path.read_text(encoding="utf-8"),
        start_url="https://www.lamudi.com.mx/distrito-federal/coyoacan/prado-churubusco/for-sale/",
    )
    assert len(rows) >= 25
    assert all(r["listing_id"] for r in rows)
