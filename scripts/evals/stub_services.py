"""
Stub httpx for offline eval runs — mirrors test_agents.py fake matching/listings.
Install before importing orchestrator agents when EVAL_STUB_SERVICES=1 (default).
"""
from __future__ import annotations

import os
import sys
import types


class _FakeResp:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, json=None):
        if "match/search" in url:
            filters = (json or {}).get("filters", {})
            results = [
                {
                    "id": "L-CDMX-001",
                    "title": "Depto Roma Norte",
                    "state": "CDMX",
                    "city": "CDMX",
                    "neighborhood": "Roma Norte",
                    "price_mxn": 6800000,
                    "beds": 2,
                    "baths": 2,
                    "m2": 95,
                    "type": "departamento",
                    "score": 0.83,
                    "media": [],
                },
                {
                    "id": "L-MOR-001",
                    "title": "Casa con alberca",
                    "state": "Morelos",
                    "city": "Cuernavaca",
                    "neighborhood": "Tabachines",
                    "price_mxn": 7900000,
                    "beds": 4,
                    "baths": 3,
                    "m2": 320,
                    "type": "casa",
                    "score": 0.71,
                    "media": [],
                },
            ]
            if filters.get("state") == "Morelos":
                results = [r for r in results if r["state"] == "Morelos"]
            elif filters.get("state") == "CDMX":
                results = [r for r in results if r["state"] == "CDMX"]
            return _FakeResp({"count": len(results), "results": results})
        return _FakeResp({})

    async def get(self, url, params=None):
        if "/listings/L-CDMX-001" in url:
            return _FakeResp({"id": "L-CDMX-001", "title": "Depto Roma Norte", "state": "CDMX"})
        if "/listings/L-MOR-001" in url:
            return _FakeResp({"id": "L-MOR-001", "title": "Casa con alberca", "state": "Morelos"})
        return _FakeResp({})


def install(*, force: bool = False) -> bool:
    """Return True if stubs were installed."""
    if not force and os.getenv("EVAL_STUB_SERVICES", "1") not in ("1", "true", "yes"):
        return False
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.AsyncClient = _FakeClient
    sys.modules["httpx"] = fake_httpx
    return True
