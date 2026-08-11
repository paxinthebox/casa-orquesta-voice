"""Load and filter shared market ingest queries."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .market_geo import expand_colonia_queries
from .portals.inmuebles24 import load_market_queries as _load_all_market_queries


def load_market_queries() -> list[dict[str, Any]]:
    return _load_all_market_queries()


def load_colonia_queries() -> list[dict[str, Any]]:
    path = Path(os.getenv("MARKET_COLONIAS_QUERIES_PATH", "data/market_queries_colonias.json"))
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return expand_colonia_queries(raw)


def filter_queries(
    queries: list[dict[str, Any]],
    *,
    colonia_only: bool = False,
    colonia_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    if colonia_only:
        queries = [q for q in queries if str(q.get("scope") or "").lower() == "colonia"]
    if colonia_names:
        wanted = {name.casefold() for name in colonia_names}
        queries = [
            q for q in queries
            if str(q.get("colonia") or "").casefold() in wanted
        ]
    return queries
