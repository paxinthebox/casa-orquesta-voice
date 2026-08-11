"""Round-robin source diversity for top-k listing results."""
from __future__ import annotations

from typing import Any


def diversify_by_source(rows: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    """
    Pick up to ``k`` rows while rotating across ``source`` values.

    Preserves score ordering within each source bucket; rare portals still
    surface when they have competitive matches. When multiple portals are
    present, always take the top scorer from each before repeating.
    """
    if k <= 0 or not rows:
        return []
    if len(rows) <= k:
        return list(rows)

    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        src = str(row.get("source") or "unknown")
        by_source.setdefault(src, []).append(row)

    sources = list(by_source.keys())
    if len(sources) <= 1:
        return rows[:k]

    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()
    idx = {src: 0 for src in sources}

    while len(chosen) < k:
        progressed = False
        for src in sources:
            if len(chosen) >= k:
                break
            bucket = by_source[src]
            cursor = idx[src]
            while cursor < len(bucket):
                row = bucket[cursor]
                cursor += 1
                rid = str(row.get("id") or "")
                if rid and rid in chosen_ids:
                    continue
                chosen.append(row)
                if rid:
                    chosen_ids.add(rid)
                progressed = True
                break
            idx[src] = cursor
        if not progressed:
            break

    if len(chosen) < k:
        for row in rows:
            if len(chosen) >= k:
                break
            rid = str(row.get("id") or "")
            if rid and rid in chosen_ids:
                continue
            chosen.append(row)
            if rid:
                chosen_ids.add(rid)

    return chosen
