"""
Casa·Orquesta · Voice — Orchestrator Harness.

This file is the *harness*: the single entry point that wires the Claude Agent
SDK together. It loads the three agents, registers their tools, attaches the
four hooks (PII redaction, spend caps, audit log, trace emission), and exposes
a streaming `run()` for the voice gateway plus a non-streaming `run_once()` for
HTTP fall-back.

Status: skeleton. Phase 1 of `docs/PLAN.md` fills this in with the real SDK calls.

Design contract (must hold after Phase 1):
1. Behavior matches the 161 assertions ported from casa-orquesta-mvp.
2. Every tool call passes through the four hooks.
3. Streaming yields token-level events to the voice gateway under 600 ms first-token P50.
4. Without ANTHROPIC_API_KEY, the harness runs in simulated mode with the same trace shape.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

# In Phase 1 this becomes a real SDK import. Until then, agents/* and tools/*
# can be exercised standalone via the simulated path.
# from claude_agent_sdk import Agent, AgentRun, tool, agent_as_tool

from agents.realestate import realestate_agent
from agents.locator import locator_agent
from agents.audit import audit_agent
from hooks import audit_log, pii_redaction, spend_caps, trace, prompt_guard

USE_REAL_AI = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


# ====================================================================
# Registry — the three agents and their hooks
# ====================================================================
AGENTS = {
    "realestate_agent": realestate_agent,
    "locator_agent": locator_agent,
    "audit_agent": audit_agent,
}

# Hook modules — the live wiring is in agents/__init__.py (Agent.run);
# this registry documents the enforcement points for the harness.
HOOKS = [
    pii_redaction,   # pre-LLM, strips INE/RFC/CURP/names/phones (redact_messages)
    prompt_guard,    # pre-LLM on user turns, blocks obvious injection/jailbreak
    spend_caps,      # pre-LLM, enforces per-tenant per-day budget (should_call_llm)
    audit_log,       # post-tool, writes to S3/Tigris WORM (record)
    trace,           # post-step, emits /agents.html-compatible events (emit)
]


# ====================================================================
# Streaming entry point — voice gateway calls this
# ====================================================================
async def run_stream(
    message: str,
    state: dict,
    tenant_id: str,
    user_id: str,
) -> AsyncIterator[dict]:
    """
    Stream agent reasoning to the voice gateway.

    Yields one of:
      {"kind": "agent_start",  "agent": "...", "ts_ms": int}
      {"kind": "agent_tool",   "agent": "...", "tool": "...", "args": {...}}
      {"kind": "tool_result",  "agent": "...", "tool": "...", "data": {...}}
      {"kind": "text_delta",   "agent": "...", "text": "..."}        # TOKEN-LEVEL
      {"kind": "agent_end",    "agent": "...", "output": "..."}
      {"kind": "run_end",      "run_id": "...", "ai_mode": "real|sim"}

    The voice gateway concatenates text_delta into TTS-friendly phrases.
    """
    # PHASE 1: Replace with claude_agent_sdk.AgentRun(...).stream()
    # PHASE 1 — Simulated fallback (preserves the MVP test contract):
    raise NotImplementedError("Phase 1 deliverable — see docs/PLAN.md")


# ====================================================================
# Non-streaming entry point — HTTP /chat, /agent/run
# ====================================================================
async def run_once(
    message: str,
    state: dict,
    tenant_id: str,
    user_id: str,
) -> dict:
    """
    One-shot entry point. Equivalent to consuming run_stream() to completion.

    Returns:
        {
          "run_id":         str,
          "reply":          str,
          "data":           dict | None,
          "agents_invoked": list[str],
          "trace":          list[TraceStep],
          "ai_mode":        "real-claude" | "simulated",
          "state":          dict,
        }
    """
    raise NotImplementedError("Phase 1 deliverable — see docs/PLAN.md")


def list_agents_meta() -> list[dict]:
    """
    Return the agent + tool registry — wire-compatible with the MVP's
    /api/orchestrator/agents endpoint.
    """
    return [
        {
            "name": a.name,
            "description": a.description,
            "model": a.model,
            "tools": [{"name": t.name, "description": t.description} for t in a.tools],
        }
        for a in [realestate_agent, locator_agent, audit_agent]
    ]
