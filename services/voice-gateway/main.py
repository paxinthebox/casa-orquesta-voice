"""
Casa·Orquesta · Voice — Voice Gateway Service.

WebSocket-based bidirectional audio pipeline:
  mobile mic frames → Deepgram STT → orchestrator (SSE) → ElevenLabs TTS → mobile speaker

Phase 2.3 (P2.3): pipeline glue + barge-in + reconnect.
  * `pipeline.VoiceSession` owns the per-session orchestration.
  * `SessionRegistry` lets a dropped WS resume by reconnecting to
    `/voice/{tenant_id}/{user_id}/{session_id}`.
  * Barge-in detector fires `cancel` within 200 ms of user speech.

Latency budget (P50, see ARCHITECTURE.md):
  + 20 ms   client → gateway via WSS
  + 200 ms  STT partial available
  + 500 ms  end-of-speech detected (Deepgram VAD)
  + 800 ms  first agent token (Haiku-routed)
  + 1100 ms first TTS audio frame ready
  + 1500 ms audible response begins

Acceptance gate: P50 ≤ 1.5 s, P95 ≤ 2.5 s, barge-in within 200 ms, WS recovers
from forced disconnect within 1 s.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from stt.deepgram_client import DeepgramSTT
from tts.factory import open_tts
from orchestrator_client import OrchestratorStreamClient
from barge_in import BargeInDetector
from pipeline import REGISTRY, VoiceSession

app = FastAPI(title="Casa·Orquesta Voice Gateway", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:8000")
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "es-MX")
TTS_VOICE = os.getenv("TTS_VOICE", "")  # provider chooses default if blank
# Phase 4.8 WS-auth gate. Default ON in production. Local dev sets the env
# to "0" to bypass — the same toggle as identity's INVITE_REQUIRED so
# the dev story is consistent across services.
VOICE_REQUIRE_AUTH = os.getenv("CO_VOICE_REQUIRE_AUTH", "1") == "1"


# Phase 4.8 — lazy import of the WS auth helper. We do this here so a
# missing _shared path (test sandbox) doesn't crash module import.
_ws_verify = None
try:
    import sys as _sys_pre
    import os as _os_pre
    _shared_pre = _os_pre.path.normpath(
        _os_pre.path.join(_os_pre.path.dirname(__file__), "..", "_shared")
    )
    if _shared_pre not in _sys_pre.path:
        _sys_pre.path.insert(0, _shared_pre)
    from auth_middleware import verify_ws_token as _ws_verify  # noqa: E402
    from auth_middleware import WS_CLOSE_AUTH_FAILED as _WS_AUTH_FAIL  # noqa: E402
except Exception as _e:                                          # pragma: no cover
    print(f"[voice-gateway] WS auth helper not loaded: {_e!r}")
    _WS_AUTH_FAIL = 4401  # type: ignore[assignment]


async def _authenticate_ws(
    ws: WebSocket, *, expected_tenant_id: str, expected_user_id: str,
) -> bool:
    """
    Validate the WS handshake's bearer token BEFORE ws.accept().

    Returns True if the request is allowed to proceed (or if auth is
    disabled via env). On failure, closes the WS with code 4401 and
    returns False.
    """
    if not VOICE_REQUIRE_AUTH or _ws_verify is None:
        return True
    qs = ws.scope.get("query_string", b"").decode("latin-1")
    subprotos = ws.headers.get("sec-websocket-protocol", "")
    # Subprotocols may be comma-separated; take the first that starts with bearer.
    chosen_subproto = ""
    for sp in [s.strip() for s in subprotos.split(",") if s.strip()]:
        if sp.startswith("bearer.") or sp.startswith("bearer-"):
            chosen_subproto = sp
            break
    try:
        await _ws_verify(
            query_string=qs,
            subprotocol=chosen_subproto,
            require_tenant_id=expected_tenant_id,
            require_user_id=expected_user_id,
        )
    except Exception as e:
        print(f"[voice] auth rejected for tenant={expected_tenant_id} "
              f"user={expected_user_id}: {e!r}")
        try:
            await ws.close(code=_WS_AUTH_FAIL)
        except Exception:
            pass
        return False
    return True


@app.get("/health")
def health():
    from tts.elevenlabs_client import resolve_voice_id
    return {
        "status": "ok",
        "stt": "deepgram-nova-2-es-mx",
        "tts": os.getenv("TTS_PROVIDER", "elevenlabs"),
        "tts_voice_id": resolve_voice_id(TTS_VOICE or None),
        "orchestrator": ORCHESTRATOR_URL,
        "active_sessions": len(REGISTRY),
    }


@app.get("/sessions")
def list_sessions():
    """Diagnostic — list active session ids and their basic state."""
    return {
        "count": len(REGISTRY),
        "sessions": [
            {
                "session_id": sid,
                "tenant_id": s.tenant_id,
                "user_id": s.user_id,
                "stats": dict(s.stats),
            }
            for sid, s in REGISTRY._sessions.items()  # noqa: SLF001
        ],
    }


# ============================================================================
# Voice WebSocket — new session
# ============================================================================
@app.websocket("/voice/{tenant_id}/{user_id}")
async def voice_session(ws: WebSocket, tenant_id: str, user_id: str):
    """
    Start a brand-new voice session. The server assigns a `session_id`
    and announces it in the first outbound `hello` event.
    """
    session_id = f"S-{uuid.uuid4().hex[:10].upper()}"
    await _run_session(ws, tenant_id=tenant_id, user_id=user_id,
                       session_id=session_id, resume=False)


# ============================================================================
# Voice WebSocket — resume an existing session
# ============================================================================
@app.websocket("/voice/{tenant_id}/{user_id}/{session_id}")
async def voice_session_resume(
    ws: WebSocket, tenant_id: str, user_id: str, session_id: str
):
    """
    Resume an existing session after a WS drop. The conversation state,
    STT, and TTS connections stay alive in the registry; we just swap
    in the new outbound hooks.
    """
    await _run_session(ws, tenant_id=tenant_id, user_id=user_id,
                       session_id=session_id, resume=True)


# ----------------------------------------------------------------------------
# Shared session driver
# ----------------------------------------------------------------------------
async def _run_session(
    ws: WebSocket,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    resume: bool,
) -> None:
    # Phase 4.8 — validate the bearer token BEFORE accepting. Identity
    # comes from the JWT, not the URL path, but the URL path must match
    # the JWT claims so we don't accept a token whose tenant/user differs
    # from what the route advertises.
    if not await _authenticate_ws(
        ws, expected_tenant_id=tenant_id, expected_user_id=user_id,
    ):
        return

    await ws.accept()

    async def send_text(payload: dict) -> None:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_bytes(b: bytes) -> None:
        await ws.send_bytes(b)

    session: Optional[VoiceSession] = REGISTRY.get(session_id) if resume else None

    if session is None:
        # Build a fresh session. Factories defer real network calls
        # until `session.open()`.
        async def stt_factory(on_partial, on_final):
            return await DeepgramSTT.open(
                language=STT_LANGUAGE,
                on_partial=on_partial, on_final=on_final,
            )

        async def tts_factory(on_audio):
            return await open_tts(voice=TTS_VOICE or None, on_audio=on_audio)

        orch = OrchestratorStreamClient(
            ORCHESTRATOR_URL, tenant_id=tenant_id, user_id=user_id,
        )
        session = VoiceSession.create(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            stt_factory=stt_factory,
            tts_factory=tts_factory,
            orch=orch,
            send_text=send_text,
            send_bytes=send_bytes,
            barge=BargeInDetector(),
        )
        REGISTRY.put(session)
        await session.open()
    else:
        # Resume — keep the existing state, just attach new hooks.
        await session.attach_socket(send_text, send_bytes)

    print(f"[voice] session {session_id} {'resumed' if resume else 'opened'} "
          f"for tenant={tenant_id} user={user_id}")

    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.handle_audio(msg["bytes"])
            elif msg.get("text"):
                try:
                    ctrl = json.loads(msg["text"])
                except (ValueError, TypeError):
                    ctrl = {}
                await session.handle_control(ctrl)
    except WebSocketDisconnect:
        print(f"[voice] session {session_id} disconnected — keeping in registry "
              f"for resume")
    except Exception as e:
        print(f"[voice] session {session_id} error: {e!r}")
        try:
            await send_text({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        # Drop is recoverable: detach hooks but leave the session in
        # the registry for the TTL window. The client can reconnect to
        # `/voice/{tenant}/{user}/{session_id}`.
        await session.detach_socket()


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
    print(f"[voice-gateway] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


def _vg_dsar_gather(user_id: str, tenant_id: str) -> dict:
    """List active VoiceSessions belonging to the user."""
    sessions = []
    # REGISTRY is private; we access it via the same internal hook used by
    # /sessions diagnostic above.
    for sid, s in REGISTRY._sessions.items():                 # noqa: SLF001
        if s.user_id == user_id and s.tenant_id == tenant_id:
            sessions.append({
                "session_id": sid,
                "tenant_id": s.tenant_id,
                "stats": dict(s.stats),
                "state": dict(s.state),
            })
    return {
        "sessions": sessions,
        "summary": {"session_count": len(sessions)},
    }


def _vg_dsar_purge(user_id: str, tenant_id: str) -> dict:
    """Tear down + evict every VoiceSession belonging to the user.

    Returns a synchronous receipt with the count; the session.close()
    coroutines are fired-and-forgotten so a slow STT/TTS teardown
    doesn't block the DSAR response.
    """
    import asyncio as _asyncio
    doomed = [
        sid for sid, s in REGISTRY._sessions.items()          # noqa: SLF001
        if s.user_id == user_id and s.tenant_id == tenant_id
    ]
    for sid in doomed:
        session = REGISTRY._sessions.get(sid)                 # noqa: SLF001
        if session is not None:
            try:
                _asyncio.create_task(session.close())
            except Exception:
                pass
        REGISTRY.evict(sid)
    return {
        "count": len(doomed),
        "deleted": True,
        "detail": {"session_ids": doomed[:50]},
    }


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_vg_dsar_gather,
        purge=_vg_dsar_purge,
        service_name="voice-gateway",
    )
