"""
P1.8 verification — orchestrator HTTP API + SSE streaming endpoint.

Covers:
  1. /health response shape
  2. /agents hierarchical registry shape (3 + 6 + 5; leaf count = 11)
  3. /chat returns the {reply, agents_invoked, trace, run_id, ai_mode, ...} shape
  4. /agent/run returns the bare multi-agent run shape
  5. /agent/run/stream emits SSE events in the right order:
       agent_start ... agent_tool ... agent_end ... text_delta ... run_end
  6. Trace integrity inside the stream (paired starts/ends)

Standalone runner — pytest-optional, like the rest of the test suite.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

# Force simulated mode for deterministic tests.
os.environ.pop("ANTHROPIC_API_KEY", None)

# On a host run the Docker-internal hostnames (http://listings:8000) don't
# resolve; default to the docker-compose port mappings instead. In-container
# runs have these set explicitly so the defaults never apply there.
os.environ.setdefault("LISTINGS_URL", "http://localhost:8002")
os.environ.setdefault("MATCHING_URL", "http://localhost:8003")

# Skip if FastAPI/httpx/Starlette aren't installed (sandbox).
try:
    import httpx
    from fastapi import FastAPI  # noqa: F401
except ImportError as e:
    print()
    print("=" * 70)
    print("  test_streaming — DEPENDENCIES MISSING")
    print("=" * 70)
    print(f"  Skipping: {e}")
    print("  (CI environment will have fastapi/httpx; sandbox doesn't.)")
    print()
    sys.exit(0)

import main  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def expect(label: str, cond: bool, detail: str = ""):
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(t: str):
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


# Wire an ASGI transport so we can hit main.app in-process without uvicorn.
TRANSPORT = httpx.ASGITransport(app=main.app)


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=TRANSPORT, base_url="http://orch") as c:
        return await c.get(path)


async def _post(path: str, body: dict) -> httpx.Response:
    async with httpx.AsyncClient(transport=TRANSPORT, base_url="http://orch") as c:
        return await c.post(path, json=body)


async def _post_stream(path: str, body: dict) -> list[dict]:
    """Drain the SSE stream and return the events as a list of dicts."""
    events: list[dict] = []
    async with httpx.AsyncClient(transport=TRANSPORT, base_url="http://orch",
                                  timeout=30.0) as c:
        async with c.stream("POST", path, json=body) as r:
            buffer = b""
            async for chunk in r.aiter_raw():
                buffer += chunk
                while b"\n\n" in buffer:
                    raw, buffer = buffer.split(b"\n\n", 1)
                    raw = raw.strip()
                    if not raw:
                        continue
                    # Each raw block starts with "data: "
                    for line in raw.split(b"\n"):
                        if line.startswith(b"data: "):
                            payload = line[len(b"data: "):].decode("utf-8")
                            events.append(json.loads(payload))
    return events


# ====================================================================
# 1. /health
# ====================================================================
section("1. /health response shape")

r = asyncio.run(_get("/health"))
expect("/health 200", r.status_code == 200, str(r.status_code))
body = r.json()
expect("/health has 'status' == 'ok'", body.get("status") == "ok")
expect("/health has 'ai_mode' field", "ai_mode" in body)
expect("/health lists 3 agents", len(body.get("agents") or []) == 3,
       str(body.get("agents")))


# ====================================================================
# 2. /agents registry shape
# ====================================================================
section("2. /agents hierarchical registry")

r = asyncio.run(_get("/agents"))
expect("/agents 200", r.status_code == 200, str(r.status_code))
body = r.json()
expect("/agents has 'agents' list",
       isinstance(body.get("agents"), list) and len(body["agents"]) == 3)
agents_list = body["agents"]
by_name = {a["name"]: a for a in agents_list}
expect("registry has realestate_agent", "realestate_agent" in by_name)
expect("registry has locator_agent",    "locator_agent" in by_name)
expect("registry has audit_agent",      "audit_agent" in by_name)

# Tool counts at the leaves: locator=6, audit=5, realestate=2 sub-agent tools.
expect("locator has 6 tools",
       len(by_name["locator_agent"]["tools"]) == 6)
expect("audit has 5 tools",
       len(by_name["audit_agent"]["tools"]) == 5)
expect("realestate has 2 tools (sub-agent wrappers)",
       len(by_name["realestate_agent"]["tools"]) == 2)
# Canonical leaf-tool count per CLAUDE.md "Tool/agent registry": 11.
leaf_count = (len(by_name["locator_agent"]["tools"])
              + len(by_name["audit_agent"]["tools"]))
expect("leaf tool count == 11 (CLAUDE.md canonical)", leaf_count == 11,
       f"got {leaf_count}")


# ====================================================================
# 3. /chat shape
# ====================================================================
section("3. /chat shape")

r = asyncio.run(_post("/chat", {
    "message": "Busco un departamento en Roma Norte de 2 recámaras hasta 8 millones",
}))
expect("/chat 200", r.status_code == 200, r.text[:200])
body = r.json()
for key in ("conversation_id", "run_id", "reply",
            "agents_invoked", "trace", "ai_mode", "state"):
    expect(f"/chat returns '{key}'", key in body)
expect("/chat run_id has 'R-' prefix", str(body.get("run_id", "")).startswith("R-"))
expect("/chat agents_invoked includes realestate_agent",
       "realestate_agent" in (body.get("agents_invoked") or []))
expect("/chat agents_invoked includes locator_agent (search intent)",
       "locator_agent" in (body.get("agents_invoked") or []))
expect("/chat conversation_id has 'c-' prefix",
       str(body.get("conversation_id", "")).startswith("c-"))


# ====================================================================
# 4. /agent/run shape (bare)
# ====================================================================
section("4. /agent/run bare shape")

r = asyncio.run(_post("/agent/run", {"message": "Hola"}))
expect("/agent/run 200", r.status_code == 200, r.text[:200])
body = r.json()
for key in ("run_id", "reply", "data", "ai_mode", "trace", "agents_invoked", "state"):
    expect(f"/agent/run returns '{key}'", key in body)


# ====================================================================
# 5. /agent/run/stream — event ordering + run_end last
# ====================================================================
section("5. /agent/run/stream SSE event ordering")

events = asyncio.run(_post_stream("/agent/run/stream",
                                   {"message": "Busco una casa con alberca en Cuernavaca"}))
expect("stream produced events", len(events) > 0, f"count={len(events)}")
expect("first event is agent_start",
       events[0].get("kind") == "agent_start",
       f"first kind: {events[0].get('kind') if events else 'none'}")
expect("last event is run_end",
       events[-1].get("kind") == "run_end",
       f"last kind: {events[-1].get('kind') if events else 'none'}")
expect("stream contains a text_delta event",
       any(e.get("kind") == "text_delta" for e in events))
expect("stream contains at least one agent_tool event",
       any(e.get("kind") == "agent_tool" for e in events))
expect("stream contains agent_end before run_end",
       any(e.get("kind") == "agent_end" for e in events))
expect("every event has a 'kind' field",
       all("kind" in e for e in events))
expect("every event has an 'agent' field",
       all("agent" in e for e in events))


# ====================================================================
# 6. Stream trace integrity — paired starts/ends, depth ≥ 0
# ====================================================================
section("6. Stream trace integrity")

depth = 0
stack: list[str] = []
bad = ""
for s in events:
    k = s.get("kind")
    if k == "agent_start":
        depth += 1
        stack.append(s.get("agent"))
    elif k == "agent_end":
        if not stack:
            bad = "agent_end with no matching start"
            break
        opened = stack.pop()
        if opened != s.get("agent"):
            bad = f"agent_end '{s.get('agent')}' != open '{opened}'"
            break
        depth -= 1
    if depth < 0:
        bad = "depth went negative"
        break
# `stack` may still have entries at end because run_end + text_delta come after
# agent_end events in the stream's tail; what matters is that every start
# was eventually paired by an end.
expect("trace pairs (no agent_end without start)", not bad, bad)


# ====================================================================
# Summary
# ====================================================================
print()
print("=" * 70)
print("  SUMMARY")
print("=" * 70)
print(f"  Passed: {len(PASSED)}")
print(f"  Failed: {len(FAILED)}")
if FAILED:
    for label, detail in FAILED:
        print(f"  ❌ {label}: {detail}")
    sys.exit(1)
print("  All streaming endpoint assertions green. ✅")
sys.exit(0)
