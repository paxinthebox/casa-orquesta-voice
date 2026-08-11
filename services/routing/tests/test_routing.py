"""Phase 6 routing service — behavior contract."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "_shared")))
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "..")))

from main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def expect(label: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        raise AssertionError(label)


def test_health():
    client = TestClient(app)
    r = client.get("/health")
    expect("GET /health 200", r.status_code == 200)
    body = r.json()
    expect("health has provider", "provider" in body)


def test_optimize_haversine():
    async def fake_fetch(ids):
        return {
            "L-A": {"id": "L-A", "lat": 19.42, "lng": -99.17},
            "L-B": {"id": "L-B", "lat": 18.92, "lng": -99.23},
        }

    import main as routing_main

    routing_main._fetch_listing_coords = fake_fetch  # type: ignore

    client = TestClient(app)
    r = client.post(
        "/route/optimize",
        json={
            "origin": {"lat": 19.4153, "lng": -99.1654, "type": "user_location"},
            "listing_ids": ["L-A", "L-B"],
        },
    )
    expect("POST /route/optimize 200", r.status_code == 200, r.text)
    body = r.json()
    expect("ordered_listing_ids present", len(body.get("ordered_listing_ids", [])) == 2)
    expect("legs present", len(body.get("legs", [])) == 2)
    expect("total_distance_m > 0", body.get("total_distance_m", 0) > 0)


def main() -> None:
    print("=" * 60)
    print("  Phase 6 — routing service")
    print("=" * 60)
    test_health()
    test_optimize_haversine()
    print("\n  SUMMARY  passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
