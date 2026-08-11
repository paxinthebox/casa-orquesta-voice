"""
Casa·Orquesta · Voice — Multi-agent layer (Claude Agent SDK harness).

Phase 1.6 deliverable: full Agent.run() machinery + the run_orchestrator()
entry point. Tools are now real (P1.6 deliverable), routed by the simulated
dispatch tables in `_simulated.py` when ANTHROPIC_API_KEY is unset, and by
the SDK's tool-use loop when it's set.

P1.7 will add the hooks (PII redaction, spend caps, audit log, trace).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
USE_REAL_AI = bool(ANTHROPIC_KEY)
DEFAULT_TENANT_ID = os.getenv("TENANT_ID", "default")
MAX_AGENT_DEPTH = 3
MAX_TOOL_CALLS_PER_AGENT = 6

# Hook integration — Phase 1.7. Imports are guarded so a circular-import
# during package construction can't bring down the module.
try:
    from hooks import pii_redaction, spend_caps, audit_log, trace as trace_hook, prompt_guard
    _HOOKS_AVAILABLE = True
except Exception as _hook_e:  # pragma: no cover
    print(f"[agents] hooks unavailable: {_hook_e!r}")
    _HOOKS_AVAILABLE = False
    pii_redaction = spend_caps = audit_log = trace_hook = prompt_guard = None  # type: ignore

__all__ = [
    "Tool", "Agent", "RunContext", "TraceStep",
    "REALESTATE", "LOCATOR", "AUDIT",
    "AGENTS",
    "list_agents_meta",
    "run_orchestrator",
]


# ====================================================================
# Core types
# ====================================================================
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict, "RunContext"], Awaitable[Any]]

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class TraceStep:
    kind: str  # "agent_start" | "agent_tool" | "agent_end" | "text_delta"
    agent: str
    detail: dict
    ts_ms: int


# ====================================================================
# Streaming helper — Phase 5.2
#
# Both the real (Anthropic SDK streaming) path and the simulated path
# call `stream_text_through_emit` so the wire contract is identical:
# multiple `text_delta` events arrive in order, and their concatenated
# `detail["text"]` equals the final reply.
#
# The chunker splits on punctuation + word-count to mirror the gateway's
# `PhraseChunker` (services/voice-gateway/tts/phrase_chunker.py). That
# means each delta is *approximately* one synthesis unit — the TTS can
# start emitting audio as soon as the first delta lands.
# ====================================================================
def _split_text_for_streaming(
    text: str, *, target_words: int = 4, max_chars: int = 80,
) -> list[str]:
    """Split text into deltas suitable for streaming + TTS pickup.

    Rules:
      - Break on punctuation (`.`, `!`, `?`, `,`, `;`, `:`).
      - If a span exceeds `target_words` without punctuation, break on
        the next word boundary.
      - Hard cap at `max_chars` so a single delta can't dominate.
      - Preserve trailing whitespace so concatenation reproduces the
        original text exactly.
    """
    import re
    if not text:
        return []
    out: list[str] = []
    cur: list[str] = []
    cur_chars = 0
    cur_words = 0
    # Tokens are runs of non-space, with the trailing whitespace attached.
    for tok in re.findall(r"\S+\s*", text):
        cur.append(tok)
        cur_chars += len(tok)
        # A word is anything with at least one alphanumeric character.
        if any(c.isalnum() for c in tok):
            cur_words += 1
        stripped = tok.rstrip()
        ends_clause = stripped and stripped[-1] in ".!?,;:"
        if ends_clause or cur_words >= target_words or cur_chars >= max_chars:
            out.append("".join(cur))
            cur, cur_chars, cur_words = [], 0, 0
    if cur:
        out.append("".join(cur))
    return out


def _run_used_tool(ctx: "RunContext", tool_name: str) -> bool:
    """True if *tool_name* was invoked anywhere in this run (incl. sub-agents)."""
    steps = ctx.wire if ctx.wire is not None else ctx.trace
    for step in steps:
        if step.kind not in ("tool_result", "agent_tool"):
            continue
        if (step.detail or {}).get("tool") == tool_name:
            return True
    return False


def stream_text_through_emit(
    ctx: "RunContext", agent_name: str, text: str,
    *, target_words: int = 4, max_chars: int = 80,
) -> None:
    """Emit a sequence of `text_delta` events for the given text.

    Used by both the real (Anthropic SDK streaming wrapper) and the
    simulated paths so the wire protocol is identical.
    """
    for delta in _split_text_for_streaming(
        text, target_words=target_words, max_chars=max_chars,
    ):
        ctx.emit("text_delta", agent_name, {"text": delta})


@dataclass
class RunContext:
    run_id: str
    state: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    depth: int = 0
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    # P5.3: run-wide event sink shared across parent and sub-agent
    # contexts. The parent's *recorded* `trace` stays summarized (one
    # `subagent_run` step per delegation), while `wire` carries every
    # event chronologically — it's what run_orchestrator returns to SSE
    # consumers, preserving the MVP trace contract.
    wire: Optional[list] = None

    def emit(self, kind: str, agent: str, detail: dict) -> None:
        step = TraceStep(
            kind=kind, agent=agent, detail=detail,
            ts_ms=int(time.time() * 1000) - self.started_at_ms,
        )
        self.trace.append(step)
        if self.wire is not None:
            self.wire.append(step)
        # Phase 1.7 — fan out to the trace subscribers (Langfuse, SSE, audit).
        if _HOOKS_AVAILABLE:
            try:
                trace_hook.emit({
                    "kind": step.kind, "agent": step.agent,
                    "ts_ms": step.ts_ms, "detail": step.detail,
                    "run_id": self.run_id,
                })
            except Exception as e:
                # Never break the agent loop on a hook failure.
                print(f"[agents] trace fanout error: {e!r}")


@dataclass
class Agent:
    name: str
    description: str
    system: str
    tools: list[Tool]
    model: str

    def tool_map(self) -> dict[str, Tool]:
        return {t.name: t for t in self.tools}

    def effective_system(self, ctx: RunContext) -> str:
        """Runtime system prompt — realestate_agent adds role-specific proactivity."""
        if self.name != "realestate_agent":
            return self.system
        from agents.guardrails import role_proactivity

        role = ctx.state.get("client_role", "buyer")
        if role not in ("buyer", "seller"):
            role = "buyer"
        return f"{self.system}\n{role_proactivity(role)}"

    async def run(self, message: str, ctx: RunContext) -> dict:
        """Run the agent. Returns {'output': str, 'data': dict|None}."""
        if ctx.depth > MAX_AGENT_DEPTH:
            return {"output": f"[max-depth] {self.name} not invoked.", "data": None}

        ctx.emit("agent_start", self.name, {
            "input": message, "model": self.model,
            "tools": [t.name for t in self.tools],
        })

        # Hook — prompt guard on user-facing turns (depth 0).
        if ctx.depth == 0 and _HOOKS_AVAILABLE:
            guard_decision = prompt_guard.evaluate(message)
            if not guard_decision.get("allow"):
                ctx.emit("policy", self.name, prompt_guard.trace_event(guard_decision))
                refusal = guard_decision.get("response") or prompt_guard.REFUSAL_MESSAGE
                stream_text_through_emit(ctx, self.name, refusal)
                ctx.emit("agent_end", self.name, {"output_preview": refusal[:280]})
                return {"output": refusal, "data": None}

        # Snapshot text_delta count so we can detect whether the run
        # itself already streamed (real Anthropic path) and avoid a
        # double-stream below.
        deltas_before = sum(1 for s in ctx.trace if s.kind == "text_delta"
                            and s.agent == self.name)

        use_simulated = not USE_REAL_AI
        if self.name == "locator_agent":
            from people_intent import implies_people_search
            intent_msg = str(ctx.state.get("user_query") or message)
            if implies_people_search(intent_msg):
                use_simulated = True
                message = intent_msg

        if use_simulated:
            result = await self._run_simulated(message, ctx)
        else:
            try:
                result = await self._run_real(message, ctx)
            except Exception as e:
                print(f"[agents] {self.name} real-AI error: {e!r}; falling back.")
                result = await self._run_simulated(message, ctx)

        # Spoken reply must match listing cards only when this turn searched properties.
        if self.name == "realestate_agent" and _run_used_tool(ctx, "search_listings"):
            tool_data: dict[str, Any] = {}
            if isinstance(result, dict):
                raw_data = result.get("data")
                if isinstance(raw_data, dict):
                    tool_data = raw_data
            candidates = ctx.state.get("last_candidates") or tool_data.get("results") or []
            if candidates and "last_candidates" not in ctx.state:
                ctx.state["last_candidates"] = candidates
            from reply_synth import format_listing_search_reply

            match_quality = (
                ctx.state.get("last_match_quality")
                or tool_data.get("match_quality")
            )
            relaxations = (
                ctx.state.get("last_relaxations")
                or tool_data.get("relaxations")
                or []
            )
            if candidates and not match_quality:
                kinds = {str(c.get("match_kind") or "") for c in candidates}
                match_quality = "near" if "near" in kinds else "exact"
            match_quality = match_quality or ("none" if not candidates else "exact")
            result["output"] = format_listing_search_reply(
                filters=ctx.state.get("filters", {}),
                results=candidates,
                user_query=ctx.state.get("user_query", message),
                compact_for_cards=bool(candidates),
                match_quality=match_quality,
                relaxations=relaxations,
            )
            result["data"] = {
                "count": len(candidates),
                "results": candidates,
                "match_quality": match_quality,
                "relaxations": relaxations,
            }
            ctx.state["last_match_quality"] = match_quality
            ctx.state["last_relaxations"] = relaxations

        # P5.2 — depth-0 (top-level) agents stream their final reply on
        # the wire. The real path may have already streamed via the
        # Anthropic SDK; if so, we don't re-stream. The simulated path
        # always chunks here. Sub-agents (depth>0) never stream — the
        # supervisor wraps + reframes their output.
        deltas_after = sum(1 for s in ctx.trace if s.kind == "text_delta"
                           and s.agent == self.name)
        if (ctx.depth == 0
                and deltas_after == deltas_before
                and (result.get("output") or "").strip()):
            stream_text_through_emit(ctx, self.name, result["output"])

        ctx.emit("agent_end", self.name, {"output_preview": (result.get("output") or "")[:280]})
        return result

    # -------------------- Real Claude tool-use loop --------------------
    async def _run_real(self, message: str, ctx: RunContext) -> dict:
        import asyncio
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_KEY)
        messages: list[dict[str, Any]] = [{"role": "user", "content": message}]
        last_data = None
        tenant_id = ctx.state.get("tenant_id", DEFAULT_TENANT_ID)

        for _ in range(MAX_TOOL_CALLS_PER_AGENT):
            # Hook #5 — spend cap precheck. If exceeded, degrade gracefully.
            if _HOOKS_AVAILABLE:
                decision = spend_caps.should_call_llm(tenant_id, self.model)
                if not decision.get("allow"):
                    ctx.emit("policy", self.name, spend_caps.trace_event(decision))
                    return await self._run_simulated(message, ctx)

            # Hook #4 — PII redaction immediately before the LLM call.
            outbound = pii_redaction.redact_messages(messages) if _HOOKS_AVAILABLE else messages

            resp = client.messages.create(
                model=self.model,
                max_tokens=900,
                system=self.effective_system(ctx),
                tools=[t.to_anthropic() for t in self.tools] if self.tools else None,
                messages=outbound,
            )

            # Hook #5 — charge the tenant.
            if _HOOKS_AVAILABLE:
                try:
                    usage = getattr(resp, "usage", None)
                    if usage is not None:
                        billed = spend_caps.record_usage(
                            tenant_id, self.model,
                            getattr(usage, "input_tokens", 0),
                            getattr(usage, "output_tokens", 0),
                        )
                        ctx.emit("policy", self.name,
                                 {"kind": "policy", "policy": "spend_caps", **billed})
                except Exception as e:
                    print(f"[agents] spend_caps record_usage error: {e!r}")

            if resp.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                for block in resp.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue
                    tool = self.tool_map().get(block.name)
                    args = block.input or {}
                    ctx.emit("agent_tool", self.name,
                             {"tool": block.name, "args": args})
                    if not tool:
                        out = {"error": f"unknown tool {block.name}"}
                    else:
                        try:
                            out = await tool.handler(args, ctx)
                            last_data = out
                        except Exception as e:
                            out = {"error": str(e)}

                    # Mobile feed + audit trail: cardsStore listens for this.
                    ctx.emit("tool_result", self.name, {
                        "tool": block.name,
                        "result": out,
                    })

                    # Hook #6 — append-only audit log for every tool call.
                    if _HOOKS_AVAILABLE:
                        try:
                            audit_log.record(
                                actor=self.name,
                                action=f"tool:{block.name}",
                                resource=str(args)[:200],
                                detail={"output_preview": str(out)[:200]},
                                tenant_id=tenant_id,
                                run_id=ctx.run_id,
                            )
                        except Exception as e:
                            print(f"[agents] audit_log record error: {e!r}")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(out, ensure_ascii=False, default=str),
                    })
                messages.append({"role": "user", "content": tool_results})
                continue

            # Final text. Phase 5.2: stream it. The previous call to
            # `client.messages.create()` already produced the full text,
            # so we don't need a second round-trip — we just chunk the
            # accumulated text through the emitter so the SSE consumer
            # (voice-gateway's `PhraseChunker`) starts pulling deltas
            # immediately. Real per-token streaming via
            # `client.messages.stream()` is one short step further: same
            # contract, just emit each token from the stream as it
            # arrives. We do that path when the SDK's streaming context
            # manager is available.
            text = ""
            for block in resp.content:
                if getattr(block, "type", None) == "text":
                    text += block.text

            # If we can re-run the same request as a stream, do that so
            # the first delta lands as fast as Anthropic produces it.
            # Otherwise (e.g. tools were used + we already have the
            # accumulated final text), chunk what we have.
            try:
                used_tools = sum(
                    1 for m in messages
                    if m.get("role") == "user"
                    and isinstance(m.get("content"), list)
                    and any(
                        (c.get("type") if isinstance(c, dict) else None)
                        == "tool_result"
                        for c in m.get("content", [])
                    )
                )
            except Exception:
                used_tools = 0

            # Only the supervisor (depth 0) streams. Tool-use loops
            # that called sub-agents leave the post-hoc chunker to
            # `Agent.run()`, which emits text_deltas in the right
            # position (before agent_end). The real-streaming path
            # below only fires when no tools were used — that's when
            # we can re-issue the same request as a stream cheaply.
            if ctx.depth == 0 and used_tools == 0 \
                    and not getattr(self, "_no_restream", False):
                streamed_text = await asyncio.to_thread(
                    self._stream_final_text,
                    client, messages, ctx,
                )
                if streamed_text:
                    return {"output": streamed_text, "data": last_data}
            return {"output": text, "data": last_data}

        return {"output": "[hop-limit reached]", "data": last_data}

    def _stream_final_text(
        self, client, messages: list, ctx: "RunContext",
    ) -> str:
        """Sync helper that runs Anthropic's streaming API and emits
        text_delta events per chunk. Called via `asyncio.to_thread` so
        the SDK's sync context manager doesn't block the event loop.

        Returns the concatenated text. On any failure, returns "" and
        the caller falls back to the post-hoc chunker.
        """
        try:
            outbound = (
                pii_redaction.redact_messages(messages)
                if _HOOKS_AVAILABLE else messages
            )
            collected: list[str] = []
            with client.messages.stream(
                model=self.model,
                max_tokens=900,
                system=self.effective_system(ctx),
                messages=outbound,
            ) as stream:
                for chunk in stream.text_stream:
                    if not chunk:
                        continue
                    collected.append(chunk)
                    # Emit each chunk straight to the SSE consumer.
                    ctx.emit("text_delta", self.name, {"text": chunk})
            return "".join(collected)
        except Exception as e:
            print(f"[agents] {self.name} streaming failed: {e!r}")
            return ""

    # -------------------- Simulated fallback (overridden per agent) --------------------
    async def _run_simulated(self, message: str, ctx: RunContext) -> dict:
        return {"output": f"{self.name}: respuesta simulada", "data": None}


# ====================================================================
# Compose
# ====================================================================
from .locator import locator_agent as LOCATOR        # noqa: E402
from .audit import audit_agent as AUDIT              # noqa: E402
from .realestate import realestate_agent as REALESTATE  # noqa: E402

AGENTS: dict[str, Agent] = {
    "realestate_agent": REALESTATE,
    "locator_agent": LOCATOR,
    "audit_agent": AUDIT,
}


def list_agents_meta() -> list[dict]:
    """Hierarchical: 3 top-level agents, each carrying its own tools array."""
    return [
        {
            "name": a.name,
            "description": a.description,
            "model": a.model,
            "tools": [
                {"name": t.name, "description": t.description}
                for t in a.tools
            ],
        }
        for a in (REALESTATE, LOCATOR, AUDIT)
    ]


# ====================================================================
# Orchestrator entry point
# ====================================================================
async def run_orchestrator(
    message: str,
    state: Optional[dict] = None,
) -> dict:
    """Top-level entry to the multi-agent system. Returns reply + trace."""
    from search_intent import resolve_search_filters
    from people_intent import resolve_people_filters

    ctx = RunContext(
        run_id=f"R-{uuid.uuid4().hex[:10].upper()}",
        state=state or {},
        wire=[],  # P5.3: full chronological event stream incl. sub-agents
    )
    ctx.state.setdefault("filters", {})
    ctx.state.setdefault("client_role", "buyer")
    ctx.state["user_query"] = message
    resolved = resolve_search_filters(ctx.state, message)
    ctx.state["filters"] = resolved
    ctx.state["filters_resolved"] = resolved
    people_resolved = resolve_people_filters(ctx.state, message)
    ctx.state["people_filters"] = people_resolved
    ctx.state["people_filters_resolved"] = people_resolved
    # P5.2: `Agent.run()` itself emits the text_delta stream for the
    # top-level reply (before agent_end). We don't have to re-do it here.
    result = await REALESTATE.run(message, ctx)
    return {
        "run_id": ctx.run_id,
        "reply": result.get("output", ""),
        "data": result.get("data"),
        "ai_mode": "real-claude" if USE_REAL_AI else "simulated",
        # The wire view: every event from parent AND sub-agents, in
        # order (the MVP trace contract). ctx.trace alone would only
        # show the parent's summarized view (P5.3).
        "trace": [
            {"kind": s.kind, "agent": s.agent, "ts_ms": s.ts_ms, "detail": s.detail}
            for s in (ctx.wire if ctx.wire is not None else ctx.trace)
        ],
        "state": ctx.state,
    }
