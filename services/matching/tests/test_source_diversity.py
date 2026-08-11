"""Tests for source diversity in matching top-k."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SVC = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, SVC)

from source_diversity import diversify_by_source  # noqa: E402


def test_diversify_rotates_across_sources():
    rows = [
        {"id": "VA-1", "source": "vivanuncios", "_score": 90},
        {"id": "VA-2", "source": "vivanuncios", "_score": 85},
        {"id": "PROP-1", "source": "propiedades", "_score": 80},
        {"id": "I24-1", "source": "inmuebles24", "_score": 75},
    ]
    out = diversify_by_source(rows, 3)
    assert [r["id"] for r in out] == ["VA-1", "PROP-1", "I24-1"]


def test_diversify_single_source_is_noop_order():
    rows = [
        {"id": "VA-1", "source": "vivanuncios", "_score": 90},
        {"id": "VA-2", "source": "vivanuncios", "_score": 80},
    ]
    out = diversify_by_source(rows, 2)
    assert [r["id"] for r in out] == ["VA-1", "VA-2"]
