"""
Casa·Orquesta — Scheduling Service.

Multi-party visit calendar reconciliation. Uses a deterministic
"availability heuristic" to propose three windows. In production this calls
Google/Outlook calendar APIs with consent.

Phase 1.4 status: ported from MVP with the in-memory `visits` dict replaced
by a Redis-backed store. Behavior is unchanged from the caller's perspective.
When REDIS_URL is unset or the connection fails, the service falls back to
in-memory mode so dev environments without Redis still work.
"""
import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

COMMS_URL = os.getenv("COMMS_URL", "http://comms:8000")
REDIS_URL = os.getenv("REDIS_URL", "")
REDIS_NS = os.getenv("REDIS_NAMESPACE", "casa:scheduling")
VISIT_TTL_SECONDS = int(os.getenv("VISIT_TTL_SECONDS", str(60 * 60 * 24 * 30)))

app = FastAPI(title="Casa·Orquesta Scheduling", version="0.1.0")


# ----------------------- Storage (Redis with in-mem fallback) -----------------------
class VisitStore:
    """Redis-backed store. Falls back to in-memory dict when Redis is unavailable."""
    def __init__(self, url: str = "", namespace: str = "casa:scheduling"):
        self._mem: dict[str, dict] = {}
        self._redis = None
        self.ns = namespace
        self.mode = "memory"
        if url:
            try:
                import redis  # type: ignore
                self._redis = redis.Redis.from_url(url, decode_responses=True)
                self._redis.ping()
                self.mode = "redis"
            except Exception as e:
                print(f"[scheduling] Redis unavailable, falling back to memory: {e}")
                self._redis = None

    def _k(self, vid: str) -> str:
        return f"{self.ns}:visit:{vid}"

    def put(self, vid: str, data: dict) -> None:
        if self._redis is not None:
            self._redis.setex(self._k(vid), VISIT_TTL_SECONDS, json.dumps(data))
        else:
            self._mem[vid] = data

    def get(self, vid: str) -> Optional[dict]:
        if self._redis is not None:
            raw = self._redis.get(self._k(vid))
            return json.loads(raw) if raw else None
        return self._mem.get(vid)

    def all(self) -> list[dict]:
        if self._redis is not None:
            keys = list(self._redis.scan_iter(match=f"{self.ns}:visit:*"))
            return [json.loads(self._redis.get(k)) for k in keys if self._redis.get(k)]
        return list(self._mem.values())


visits = VisitStore(REDIS_URL, REDIS_NS)


# ----------------------- Request schemas -----------------------
class ProposeReq(BaseModel):
    listing_id: str
    buyer_id: str


class ConfirmReq(BaseModel):
    visit_id: str
    slot_index: int  # 0/1/2


def next_business_days(n: int = 5):
    """Return next N business-day-aware datetime starting tomorrow."""
    out = []
    d = datetime.now() + timedelta(days=1)
    while len(out) < n:
        if d.weekday() < 6:  # Mon-Sat (typical for MX real estate)
            out.append(d)
        d += timedelta(days=1)
    return out


@app.get("/health")
def health():
    return {"status": "ok", "store_mode": visits.mode}


@app.post("/schedule/propose")
async def propose(req: ProposeReq):
    days = next_business_days(5)
    slots = [
        {"start": days[0].replace(hour=11, minute=0, second=0, microsecond=0).isoformat(),
         "end":   days[0].replace(hour=12, minute=0, second=0, microsecond=0).isoformat()},
        {"start": days[1].replace(hour=17, minute=30, second=0, microsecond=0).isoformat(),
         "end":   days[1].replace(hour=18, minute=30, second=0, microsecond=0).isoformat()},
        {"start": days[3].replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
         "end":   days[3].replace(hour=11, minute=0, second=0, microsecond=0).isoformat()},
    ]
    vid = f"V-{uuid.uuid4().hex[:10].upper()}"
    visits.put(vid, {
        "id": vid, "listing_id": req.listing_id, "buyer_id": req.buyer_id,
        "status": "proposed", "slots": slots, "selected_slot": None,
        "proposed_at": datetime.utcnow().isoformat() + "Z",
    })
    return {"visit_id": vid, "slots": slots}


@app.post("/schedule/confirm")
async def confirm(req: ConfirmReq):
    v = visits.get(req.visit_id)
    if not v:
        raise HTTPException(404, "visit not found")
    if not (0 <= req.slot_index < len(v["slots"])):
        raise HTTPException(400, "invalid slot")
    v["selected_slot"] = v["slots"][req.slot_index]
    v["status"] = "confirmed"
    v["confirmed_at"] = datetime.utcnow().isoformat() + "Z"
    visits.put(req.visit_id, v)

    # Simulated multi-party WhatsApp confirmation
    for who, msg in [
        (v["buyer_id"], f"✅ Visita confirmada para {v['selected_slot']['start']}. Te recordaremos 24h y 2h antes."),
        ("realtor-demo", f"Nueva visita confirmada — {v['listing_id']} — {v['selected_slot']['start']}."),
        ("owner-demo",  f"Visita autorizada para {v['listing_id']} el {v['selected_slot']['start']}."),
    ]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{COMMS_URL}/comms/send",
                                  json={"channel": "whatsapp", "to": who, "body": msg})
        except Exception as e:
            print(f"[scheduling] notify {who} failed: {e}")

    return v


@app.get("/visits/{vid}")
def get_visit(vid: str):
    v = visits.get(vid)
    if not v:
        raise HTTPException(404, "visit not found")
    return v


@app.get("/visits")
def list_visits():
    return visits.all()


@app.post("/visits/{vid}/feedback")
def feedback(vid: str, body: dict):
    v = visits.get(vid)
    if not v:
        raise HTTPException(404, "visit not found")
    v["status"] = "completed"
    v["feedback"] = {
        "rating": body.get("rating", 0),
        "notes": body.get("notes", ""),
        "submitted_at": datetime.utcnow().isoformat() + "Z",
    }
    visits.put(vid, v)
    return v


# ============================================================================
# Phase 4.6 — Cross-service auth + DSAR wiring
# ============================================================================
import os as _os
import sys as _sys                                  # noqa: E402
_SHARED = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "_shared")
)
if _SHARED not in _sys.path:
    _sys.path.insert(0, _SHARED)

try:
    from auth_middleware import AuthInjector                  # noqa: E402
    from dsar_responder import mount_dsar                     # noqa: E402
    _DSAR_READY = True
except Exception as _e:                                       # pragma: no cover
    print(f"[scheduling] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


# Phase 5.1 — Protocol-driven store. The DSAR contract now routes
# through `_visits_store` so production (Postgres) and tests (in-memory)
# agree by construction. The legacy `visits` Redis path stays alive
# for back-compat until the per-tenant migrations land in ops.
from store import (                                          # noqa: E402
    build_default_store as _build_visits_store,
)

_visits_store = _build_visits_store()


def set_visits_store(store) -> None:
    """Test hook — inject the in-memory store with pinned ids/time."""
    global _visits_store
    _visits_store = store


def get_visits_store():
    return _visits_store


async def _scheduling_dsar_gather(user_id: str, tenant_id: str) -> dict:
    return await _visits_store.gather_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


async def _scheduling_dsar_purge(user_id: str, tenant_id: str) -> dict:
    """Route through the store Protocol. Returns the same `{count,
    deleted, detail}` shape the in-line P4.6 logic returned, but now
    the in-memory + Postgres paths share one implementation."""
    return await _visits_store.purge_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


# Note: the in-line tombstone-overwrite logic from P4.6 lived here
# previously. It was removed in P5.1 because the Protocol-driven
# in-memory + Postgres stores both encode the same DSAR semantics.


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_scheduling_dsar_gather,
        purge=_scheduling_dsar_purge,
        service_name="scheduling",
    )
