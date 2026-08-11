"""
Trace emission hook — Phase 1.7.

Bridges the in-process Agent.run() trace events to external observers:
  - Langfuse self-hosted (when LANGFUSE_HOST + keys set)
  - SSE stream consumer (the voice gateway / mobile SessionTrace screen)
  - Audit log (for LFPDPPP-visible decision logs)

The hook is registered as an *additional* emitter alongside RunContext.emit().
Each agent_start / agent_tool / agent_end event passes through. Failures in
any external emitter are swallowed so they can't break the agent loop.

Subscribers:
  add_subscriber(callback) — receives every TraceStep dict
  remove_subscriber(callback)
"""
from __future__ import annotations

import os
import threading
from typing import Callable

LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")

_subscribers: list[Callable[[dict], None]] = []
_lock = threading.Lock()


def add_subscriber(cb: Callable[[dict], None]) -> None:
    """Register a callback. Receives every trace step as a dict."""
    with _lock:
        _subscribers.append(cb)


def remove_subscriber(cb: Callable[[dict], None]) -> None:
    with _lock:
        if cb in _subscribers:
            _subscribers.remove(cb)


def emit(step: dict) -> None:
    """Called by Agent.run() after each ctx.emit(). Never raises."""
    with _lock:
        subs = list(_subscribers)
    for cb in subs:
        try:
            cb(step)
        except Exception as e:
            print(f"[hooks.trace] subscriber failed: {e!r}")


# ----------------- Langfuse adapter (lazy-loaded) -----------------
_langfuse_client = None
_langfuse_loaded = False


def _langfuse():
    """Lazy-init the Langfuse client. Returns None when not configured."""
    global _langfuse_client, _langfuse_loaded
    if _langfuse_loaded:
        return _langfuse_client
    _langfuse_loaded = True
    if not (LANGFUSE_HOST and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY):
        return None
    try:
        from langfuse import Langfuse  # type: ignore
        _langfuse_client = Langfuse(
            host=LANGFUSE_HOST,
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
        )
    except Exception as e:
        print(f"[hooks.trace] Langfuse client init failed: {e}")
        _langfuse_client = None
    return _langfuse_client


def _langfuse_emit(step: dict) -> None:
    client = _langfuse()
    if client is None:
        return
    try:
        # Map our trace shape to a Langfuse span.
        client.event(
            name=step.get("kind", "trace"),
            metadata={"agent": step.get("agent"), **(step.get("detail") or {})},
        )
    except Exception as e:
        print(f"[hooks.trace] Langfuse emit failed: {e}")


# Auto-register the Langfuse adapter (no-op if env unset).
add_subscriber(_langfuse_emit)


def reset_for_tests() -> None:
    """Test helper — drop all subscribers and re-register Langfuse."""
    with _lock:
        _subscribers.clear()
    add_subscriber(_langfuse_emit)
