"""Phase 6 geo helpers — behavior contract."""
from __future__ import annotations

import os
import sys

_SHARED = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)

from geo import (  # noqa: E402
    GeoPoint,
    haversine_km,
    haversine_m,
    nearest_neighbor_order,
)


def expect(label: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        raise AssertionError(label)


def main() -> None:
    print("=" * 60)
    print("  Phase 6 — shared geo")
    print("=" * 60)

    # CDMX Roma → Condesa ~1.5 km
    d = haversine_km(19.4153, -99.1654, 19.4115, -99.1678)
    expect("haversine_km Roma→Condesa in range", 0.4 < d < 3.0, str(d))

    m = haversine_m(19.4153, -99.1654, 19.4153, -99.1654)
    expect("same point distance zero", m == 0.0)

    stops: list[GeoPoint] = [
        {"id": "far", "lat": 18.92, "lng": -99.23},
        {"id": "near", "lat": 19.42, "lng": -99.17},
        {"id": "mid", "lat": 19.30, "lng": -99.20},
    ]
    order, legs, total = nearest_neighbor_order(19.4153, -99.1654, stops)
    expect("nearest-neighbor picks near first", order[0] == "near", str(order))
    expect("leg count matches stops", len(legs) == 3, str(len(legs)))
    expect("total distance positive", total > 0)
    expect("legs tagged haversine", all(leg.get("provider") == "haversine" for leg in legs))

    print("\n  SUMMARY  passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
