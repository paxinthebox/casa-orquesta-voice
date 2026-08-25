"""
Casa·Orquesta · Voice — Orchestrator HTTP API.

Phase 1.8 deliverable. Exposes the four joint contracts defined in CLAUDE.md:

  GET  /health                  service health + ai_mode
  GET  /agents                  hierarchical agent + tool registry (3 agents)
  POST /chat                    non-streaming buyer chat
  POST /agent/run               non-streaming multi-agent run
  POST /agent/run/stream        SSE stream of agent_start / agent_tool /
                                tool_result / text_delta / agent_end / run_end

Streaming is implemented as a per-request trace subscriber + async queue:
events flow into the queue as Agent.run() emits them via ctx.emit() →
hooks.trace.emit() fanout; the SSE generator drains the queue.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import agents
from hooks import trace as trace_hook
from search_intent import resolve_search_filters
from people_intent import resolve_people_filters

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
USE_REAL_AI = bool(ANTHROPIC_KEY)
MODEL_ORCHESTRATOR = os.getenv("ANTHROPIC_MODEL_ORCHESTRATOR", "accounts/fireworks/models/deepseek-v4-pro")
MODEL_DIALOG = os.getenv("ANTHROPIC_MODEL_DIALOG", "accounts/fireworks/models/deepseek-v4-flash-0731")

app = FastAPI(title="Casa·Orquesta Orchestrator", version="0.2.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# In-memory conversation cache. Phase 2+ moves this to Redis with TTL.
_conversations: dict[str, dict] = {}


# ====================================================================
# Schemas
# ====================================================================
class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    user_id: str = "demo-buyer"
    role: str = "buyer"
    locale: str = "es-MX"
    focus_listing_id: Optional[str] = None
    focus_document_id: Optional[str] = None
    focus_person_id: Optional[str] = None
    focus_person_kind: Optional[str] = None
    focus_person_name: Optional[str] = None
    tenant_id: Optional[str] = None


class ChatResponse(BaseModel):
    conversation_id: str
    run_id: str
    reply: str
    data: Optional[dict] = None
    agents_invoked: list[str] = Field(default_factory=list)
    trace: list[dict] = Field(default_factory=list)
    ai_mode: str
    state: dict = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    message: str
    state: Optional[dict] = None
    tenant_id: Optional[str] = None
    conversation_id: Optional[str] = None


# ====================================================================
# Helpers
# ====================================================================
def _build_state(req: ChatRequest, conv: dict) -> dict:
    state: dict = dict(conv.get("state") or {})
    if req.tenant_id:
        state["tenant_id"] = req.tenant_id
    if req.focus_listing_id:
        state["focus_listing_id"] = req.focus_listing_id
    if req.focus_document_id:
        state["focus_document_id"] = req.focus_document_id
    if req.focus_person_id:
        state["focus_person_id"] = req.focus_person_id
    if req.focus_person_kind:
        state["focus_person_kind"] = req.focus_person_kind
    if req.focus_person_name:
        state["focus_person_name"] = req.focus_person_name
    state["user_query"] = req.message
    resolved = resolve_search_filters(state, req.message)
    state["filters"] = resolved
    state["filters_resolved"] = resolved
    people_resolved = resolve_people_filters(state, req.message)
    state["people_filters"] = people_resolved
    state["people_filters_resolved"] = people_resolved
    return state


def _agents_invoked(trace: list[dict]) -> list[str]:
    out: list[str] = []
    for s in trace:
        if s.get("kind") == "agent_start":
            a = s.get("agent")
            if a and a not in out:
                out.append(a)
    return out


# ====================================================================
# Routes
# ====================================================================
@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "ai_mode": "real-claude" if USE_REAL_AI else "simulated",
        "model_orchestrator": MODEL_ORCHESTRATOR,
        "model_dialog": MODEL_DIALOG,
        "agents": [a["name"] for a in agents.list_agents_meta()],
    }


@app.get("/agents")
def list_agents_endpoint() -> dict:
    """Hierarchical registry, per CLAUDE.md "Tool/agent registry"."""
    return {
        "ai_mode": "real-claude" if USE_REAL_AI else "simulated",
        "agents": agents.list_agents_meta(),
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    cid = req.conversation_id or f"c-{uuid.uuid4().hex[:12]}"
    conv = _conversations.setdefault(cid, {
        "id": cid, "user_id": req.user_id, "role": req.role,
        "history": [], "state": {"filters": {}, "candidates": []},
    })

    # Carry focus IDs into sticky state.
    if req.focus_listing_id:
        conv["state"]["focus_listing_id"] = req.focus_listing_id
    if req.focus_document_id:
        conv["state"]["focus_document_id"] = req.focus_document_id
    if req.focus_person_id:
        conv["state"]["focus_person_id"] = req.focus_person_id
    if req.focus_person_kind:
        conv["state"]["focus_person_kind"] = req.focus_person_kind
    if req.focus_person_name:
        conv["state"]["focus_person_name"] = req.focus_person_name
    if req.tenant_id:
        conv["state"]["tenant_id"] = req.tenant_id

    state = _build_state(req, conv)
    result = await agents.run_orchestrator(req.message, state=state)

    conv["history"].append({"role": "user", "content": req.message})
    conv["history"].append({"role": "assistant", "content": result["reply"]})
    conv["state"].update(result.get("state") or {})

    return ChatResponse(
        conversation_id=cid,
        run_id=result["run_id"],
        reply=result["reply"],
        data=result.get("data"),
        agents_invoked=_agents_invoked(result["trace"]),
        trace=result["trace"],
        ai_mode=result["ai_mode"],
        state=conv["state"],
    )


@app.post("/agent/run")
async def agent_run(req: AgentRunRequest) -> dict:
    """Bare multi-agent entry. Used by /agents.html and integrations."""
    result = await agents.run_orchestrator(req.message, state=req.state or {})
    return {
        "run_id": result["run_id"],
        "reply": result["reply"],
        "data": result.get("data"),
        "ai_mode": result["ai_mode"],
        "trace": result["trace"],
        "agents_invoked": _agents_invoked(result["trace"]),
        "state": result.get("state") or {},
    }


def _sse(event: dict) -> bytes:
    """Format a dict as one SSE chunk. Each line is JSON; two newlines end the event."""
    return (b"data: " + json.dumps(event, ensure_ascii=False, default=str).encode("utf-8")
            + b"\n\n")


@app.post("/agent/run/stream")
async def agent_run_stream(req: AgentRunRequest, request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream of agent trace events.

    Event types emitted:
      agent_start, agent_tool, agent_end          (from ctx.emit fanout)
      tool_result                                  (synthesized by handler wrap)
      text_delta                                   (token-level — populated when
                                                   the SDK migration adds streaming
                                                   tool-use; today this is a single
                                                   final-text event at run_end)
      run_end                                      (always last)
      error                                        (only on exception)
    """
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def _subscriber(step: dict) -> None:
        try:
            queue.put_nowait(step)
        except Exception:
            pass

    trace_hook.add_subscriber(_subscriber)

    async def _runner() -> None:
        try:
            run_state = dict(req.state or {})
            # Mobile formulary: re-base filters from client_profile before merge.
            cp = run_state.get("client_profile")
            if isinstance(cp, dict) and cp:
                from client_profile import client_profile_to_filters

                baseline = client_profile_to_filters(cp)
                if baseline:
                    run_state["filters"] = {
                        **(run_state.get("filters") or {}),
                        **baseline,
                    }
            cid = req.conversation_id or run_state.get("conversation_id")
            if cid:
                cid = str(cid)
                run_state["conversation_id"] = cid
                conv = _conversations.setdefault(cid, {
                    "id": cid, "user_id": "voice", "role": "buyer",
                    "history": [], "state": {"filters": {}, "candidates": []},
                })
                conv["state"].update({
                    k: v for k, v in run_state.items()
                    if k in (
                        "filters", "focus_listing_id", "focus_document_id",
                        "last_candidates", "client_role", "client_profile",
                    )
                })
                if run_state.get("client_role") in ("buyer", "seller"):
                    conv["role"] = run_state["client_role"]
            result = await agents.run_orchestrator(req.message, state=run_state)
            if cid:
                conv_rec = _conversations.get(str(cid))
                if conv_rec:
                    conv_rec["history"].append({"role": "user", "content": req.message})
                    conv_rec["history"].append({"role": "assistant", "content": result["reply"]})
                    conv_rec["state"].update(result.get("state") or {})
            # P5.2: text_delta events now arrive *during* the run via
            # the trace subscriber:
            #   - real path  → Anthropic SDK streaming
            #   - simulated  → chunked at the end of run_orchestrator
            # No synthetic single text_delta is pushed here anymore —
            # the voice-gateway's PhraseChunker has already been
            # receiving streaming chunks and feeding TTS by the time
            # we reach run_end.
            queue.put_nowait({
                "kind": "run_end",
                "agent": "realestate_agent",
                "ts_ms": 0,
                "detail": {
                    "run_id": result["run_id"],
                    "reply": result["reply"],
                    "ai_mode": result["ai_mode"],
                    "data": result.get("data"),
                    "agents_invoked": _agents_invoked(result["trace"]),
                    "state": result.get("state") or {},
                },
                "run_id": result["run_id"],
            })
        except Exception as e:
            queue.put_nowait({
                "kind": "error",
                "agent": "realestate_agent",
                "ts_ms": 0,
                "detail": {"message": str(e)},
            })
        finally:
            queue.put_nowait(SENTINEL)

    task = asyncio.create_task(_runner())

    async def _gen() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                step = await queue.get()
                if step is SENTINEL:
                    break
                yield _sse(step)
        finally:
            trace_hook.remove_subscriber(_subscriber)
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
    }
    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)


@app.get("/conversations/{cid}")
def get_conversation(cid: str) -> dict:
    if cid not in _conversations:
        raise HTTPException(404, "conversation not found")
    return _conversations[cid]


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
    print(f"[orchestrator] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


def _orchestrator_dsar_gather(user_id: str, tenant_id: str) -> dict:
    """Return everything the orchestrator holds about (tenant, user)."""
    mine = [
        {"conversation_id": cid,
         "history": conv.get("history", []),
         "state": conv.get("state", {})}
        for cid, conv in _conversations.items()
        if conv.get("user_id") == user_id
        and (conv.get("state") or {}).get("tenant_id", tenant_id) == tenant_id
    ]
    return {
        "conversations": mine,
        "summary": {"conversation_count": len(mine)},
    }


def _orchestrator_dsar_purge(user_id: str, tenant_id: str) -> dict:
    """Drop every conversation belonging to the user. Returns receipt."""
    doomed = [
        cid for cid, conv in _conversations.items()
        if conv.get("user_id") == user_id
        and (conv.get("state") or {}).get("tenant_id", tenant_id) == tenant_id
    ]
    for cid in doomed:
        _conversations.pop(cid, None)
    return {
        "count": len(doomed),
        "deleted": True,
        "detail": {"conversation_ids": doomed[:50]},
    }


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_orchestrator_dsar_gather,
        purge=_orchestrator_dsar_purge,
        service_name="orchestrator",
    )
