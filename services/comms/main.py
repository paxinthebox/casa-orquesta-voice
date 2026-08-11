"""
Casa·Orquesta — Communications Hub.

Simulates outbound WhatsApp / SMS / email and exposes a console feed for the demo.

Phase 1.4 status: simulator path preserved verbatim from MVP; a real WhatsApp
Cloud API adapter is added behind WHATSAPP_SIMULATED=false. Selection logic:

    WHATSAPP_SIMULATED=true (default)   → mock delivery + console log
    WHATSAPP_SIMULATED=false            → real Meta Cloud API
                                          (requires WHATSAPP_CLOUD_PHONE_NUMBER_ID
                                           and WHATSAPP_CLOUD_ACCESS_TOKEN)

Failure of the real path falls back to the simulator with a flag in the response
so callers (and the audit log) can see degraded delivery.
"""
import os
import json
import uuid
from datetime import datetime
from collections import deque
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from router import (
    CommsRouter, SendRequest,
)

LOG_PATH = "/app/log/comms.jsonl"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

WHATSAPP_SIMULATED = os.getenv("WHATSAPP_SIMULATED", "true").lower() == "true"
WHATSAPP_CLOUD_PHONE_NUMBER_ID = os.getenv("WHATSAPP_CLOUD_PHONE_NUMBER_ID", "")
WHATSAPP_CLOUD_ACCESS_TOKEN = os.getenv("WHATSAPP_CLOUD_ACCESS_TOKEN", "")
WHATSAPP_GRAPH_VERSION = os.getenv("WHATSAPP_GRAPH_VERSION", "v21.0")

app = FastAPI(title="Casa·Orquesta Comms", version="0.2.0")

# Ring buffer of recent messages (for the realtime console)
recent: deque = deque(maxlen=200)

# Router singleton — replaced in tests via set_router().
_router: CommsRouter = CommsRouter()


def set_router(router: CommsRouter) -> None:
    """Test hook — swap the router (and its budgets / window) at runtime."""
    global _router
    _router = router


def get_router() -> CommsRouter:
    return _router


class SendReq(BaseModel):
    channel: str  # whatsapp | sms | email
    to: str
    body: str
    template: str | None = None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "whatsapp_simulated": WHATSAPP_SIMULATED,
        "whatsapp_cloud_configured": bool(
            WHATSAPP_CLOUD_PHONE_NUMBER_ID and WHATSAPP_CLOUD_ACCESS_TOKEN
        ),
    }


async def _send_whatsapp_cloud(to: str, body: str,
                               template: Optional[str]) -> dict:
    """Real Meta WhatsApp Cloud API call. Phase 4.3 wires up template registration."""
    if not (WHATSAPP_CLOUD_PHONE_NUMBER_ID and WHATSAPP_CLOUD_ACCESS_TOKEN):
        raise RuntimeError("WhatsApp Cloud credentials not configured")

    url = (f"https://graph.facebook.com/{WHATSAPP_GRAPH_VERSION}/"
           f"{WHATSAPP_CLOUD_PHONE_NUMBER_ID}/messages")
    headers = {"Authorization": f"Bearer {WHATSAPP_CLOUD_ACCESS_TOKEN}",
               "Content-Type": "application/json"}

    if template:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {"name": template, "language": {"code": "es_MX"}},
        }
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()


@app.post("/comms/send")
async def send(req: SendReq):
    msg = {
        "id": f"M-{uuid.uuid4().hex[:10].upper()}",
        "channel": req.channel,
        "to": req.to,
        "body": req.body,
        "template": req.template,
        "status": "delivered (simulated)",
        "ts": datetime.utcnow().isoformat() + "Z",
    }

    # Try real WhatsApp Cloud when channel=whatsapp and simulator is off.
    if req.channel == "whatsapp" and not WHATSAPP_SIMULATED:
        try:
            wa_resp = await _send_whatsapp_cloud(req.to, req.body, req.template)
            msg["status"] = "delivered (whatsapp-cloud)"
            msg["provider"] = "meta_cloud_api"
            msg["provider_response"] = wa_resp
        except Exception as e:
            msg["status"] = "delivered (simulated; cloud failed)"
            msg["provider"] = "fallback_simulator"
            msg["provider_error"] = str(e)
            print(f"[comms] WhatsApp Cloud failed, falling back: {e}")

    recent.appendleft(msg)
    # Append-only log so the demo console can replay
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(msg) + "\n")
    except Exception as e:
        print(f"[comms] log write failed: {e}")
    print(f"[comms][{req.channel}] → {req.to}: {req.body}")
    return msg


@app.get("/comms/recent")
def get_recent(limit: int = 50):
    return list(recent)[:limit]


@app.delete("/comms/recent")
def clear_recent():
    recent.clear()
    return {"ok": True}


# ============================================================================
# Phase 4.3 — Router-backed send + webhook
# ============================================================================
class SendReqV2(BaseModel):
    tenant_id: str
    to: str
    purpose: str = "generic"
    body: Optional[str] = None
    template: Optional[str] = None
    variables: list[str] = Field(default_factory=list)
    user_id: Optional[str] = None
    client_dedupe_key: Optional[str] = None


@app.post("/comms/send/v2")
async def send_v2(req: SendReqV2) -> dict:
    """Router-backed send with WhatsApp-first + SMS fallback.

    Returns the structured `RoutingResult.public()` dict — channel,
    reason, message_id, and the underlying provider responses for the
    audit log.
    """
    result = await _router.send(SendRequest(
        tenant_id=req.tenant_id,
        to=req.to,
        purpose=req.purpose,
        body=req.body,
        template=req.template,
        variables=list(req.variables),
        user_id=req.user_id,
        client_dedupe_key=req.client_dedupe_key,
    ))
    msg = {
        "id": f"M-{uuid.uuid4().hex[:10].upper()}",
        "tenant_id": req.tenant_id,
        "to": req.to,
        "purpose": req.purpose,
        "channel": result.channel,
        "success": result.success,
        "reason": result.reason,
        "message_id": result.message_id,
        "used_template": result.used_template,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    recent.appendleft(msg)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps({**msg,
                                "provider_detail": result.public()}) + "\n")
    except Exception as e:
        print(f"[comms] v2 log write failed: {e}")
    return result.public() | {"id": msg["id"]}


@app.post("/comms/webhook/whatsapp")
async def whatsapp_webhook(req: Request) -> dict:
    """Meta delivery-status callback. Updates the 24h customer window
    when a message comes *from* the user, and flips the recent ring's
    status when a message we sent gets delivered/read."""
    try:
        body = await req.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    # Inbound messages — open the window.
    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for m in value.get("messages", []) or []:
                    from_ = m.get("from")
                    if from_:
                        _router.window.record_inbound(f"+{from_}")
                # Status updates carry message id + state.
                for s in value.get("statuses", []) or []:
                    mid = s.get("id")
                    st = s.get("status")
                    if mid and st:
                        for cached in list(recent):
                            if cached.get("message_id") == mid:
                                cached["status"] = st
                                break
    except Exception as e:
        print(f"[comms] webhook parse error: {e!r}")
        return {"ok": False, "error": str(e)}

    return {"ok": True}


@app.get("/comms/window/{phone}")
def window_status(phone: str) -> dict:
    return {
        "phone": phone,
        "open": _router.window.is_open(phone),
        "remaining_seconds": _router.window.remaining_seconds(phone),
    }


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
    print(f"[comms] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


def _comms_dsar_gather(user_id: str, tenant_id: str) -> dict:
    """Export messages we sent on behalf of (tenant_id) targeting the
    user. The ring buffer is best-effort — older messages may have
    rolled off (the LFPDPPP responder is supposed to acknowledge what
    we still have, not what we ever had)."""
    msgs = [
        m for m in recent
        if m.get("tenant_id") == tenant_id
        and (m.get("user_id") == user_id or m.get("to") in
             {user_id, f"+{user_id}"})
    ]
    return {
        "messages": list(msgs),
        "summary": {"message_count": len(msgs),
                    "ring_capacity": recent.maxlen},
    }


def _comms_dsar_purge(user_id: str, tenant_id: str) -> dict:
    """Drop matching entries from the ring buffer. We don't try to
    purge upstream provider state (Meta + Twilio store delivery logs
    independently); a future DPA fan-out hook calls their delete-data
    APIs."""
    doomed = [
        m for m in recent
        if m.get("tenant_id") == tenant_id
        and (m.get("user_id") == user_id or m.get("to") in
             {user_id, f"+{user_id}"})
    ]
    for m in doomed:
        try: recent.remove(m)
        except ValueError: pass
    return {
        "count": len(doomed),
        "deleted": True,
        "detail": {"provider_logs": "see upstream DPA — Meta + Twilio"},
    }


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_comms_dsar_gather,
        purge=_comms_dsar_purge,
        service_name="comms",
    )
