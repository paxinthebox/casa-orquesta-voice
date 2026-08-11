"""Reply copy when matching relaxes filters."""
from __future__ import annotations

from reply_synth import format_listing_search_reply


def test_near_match_reply_mentions_relaxation():
    msg = format_listing_search_reply(
        filters={
            "listing_mode": "rent",
            "state": "Morelos",
            "city": "Cuernavaca",
            "price_max_mxn": 16_000,
        },
        results=[{"id": "I24-1", "title": "Casa", "price_mxn": 20_000, "listing_mode": "rent"}],
        match_quality="near",
        relaxations=["price_max_mxn"],
        compact_for_cards=True,
    )
    assert "exactos" in msg
    assert "presupuesto" in msg


def test_exact_match_reply_unchanged():
    msg = format_listing_search_reply(
        filters={"listing_mode": "rent", "state": "CDMX"},
        results=[{"id": "I24-1", "title": "Depto", "price_mxn": 20_000, "listing_mode": "rent"}],
        compact_for_cards=True,
    )
    assert "Listo, encontré" in msg
    assert "exactos" not in msg
