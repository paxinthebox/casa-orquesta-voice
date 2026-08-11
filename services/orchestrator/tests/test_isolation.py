"""
P5.3 — Sub-agent context isolation.

Verifies:
  1. State writes inside a sub-agent's run() do NOT leak into the
     parent's `ctx.state`. The parent's state stays exactly what it
     was before the sub-agent call (modulo parent-side pre-call writes
     for the focus pins).
  2. The parent's `ctx.trace` records a SINGLE `subagent_run` summary
     step per sub-agent invocation — not the full child event list.
     The child's own events still went out live via `trace_hook.emit()`
     to any SSE subscriber, but the parent's persisted trace stays
     summarized.
  3. Explicit `state_delta` propagation: when a sub-agent's result
     dict carries `state_delta: {key: value}`, only those keys are
     copied back into parent state.
  4. Parent-side pre-call writes (focus_listing_id / focus_document_id
     from the handler args) flow DOWN to the child via the deepcopy
     so sub-agents that read those keys still work.
  5. Cross-tenant guard: focus pin set during a sub-agent call doesn't
     bleed across run_orchestrator invocations (each call gets its own
     RunContext).

Standalone runner. No pytest. No network.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent
sys.path.insert(0, str(SVC))

# ---------------------------------------------------------------------
# Stub external deps (same pattern as test_streaming_v2.py)
# ---------------------------------------------------------------------
import types as _types

class _FakeResp:
    def __init__(self, j, status=200):
        self._j = j
        self.status_code = status
    def json(self): return self._j
    def raise_for_status(self): pass

class _FakeClient:
    def __init__(self, *a, **kw): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass
    async def post(self, url, json=None):
        return _FakeResp({"count": 0, "results": []})
    async def get(self, url, params=None):
        return _FakeResp({})

_fake_httpx = _types.ModuleType("httpx")
_fake_httpx.AsyncClient = _FakeClient
sys.modules["httpx"] = _fake_httpx

_fake_anthropic = _types.ModuleType("anthropic")
class _AnthropicStub:
    def __init__(self, **kw): pass
_fake_anthropic.Anthropic = _AnthropicStub
sys.modules["anthropic"] = _fake_anthropic

os.environ.pop("ANTHROPIC_API_KEY", None)

from agents import (                                         # noqa: E402
    Agent, RunContext, run_orchestrator,
)
from agents.realestate import _agent_as_tool                 # noqa: E402


PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def expect(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  ✅ {label}")
    else:
        FAILED.append((label, detail))
        print(f"  ❌ {label}  ← {detail}")


def section(t: str) -> None:
    print()
    print("=" * 70)
    print(f"  {t}")
    print("=" * 70)


# ====================================================================
# Helpers — build a stub child agent we can drive deterministically
# ====================================================================
def _make_stub_child(*, on_run=None, returns=None) -> Agent:
    """Construct a sub-agent that, when invoked, runs `on_run(ctx)` and
    returns `returns` (or a default dict)."""
    async def sim(self, message, ctx):
        if on_run:
            on_run(ctx)
        if returns is not None:
            return returns
        return {"output": "stub sub-agent reply", "data": None}

    a = Agent(
        name="stub_child",
        description="test stub",
        system="stub",
        tools=[],
        model="stub-model",
    )
    a._run_simulated = sim.__get__(a, Agent)              # type: ignore[attr-defined]
    return a


def _new_parent_ctx(**state) -> RunContext:
    return RunContext(
        run_id="R-TEST", state=dict(state),
        trace=[], depth=0, started_at_ms=0,
    )


# ====================================================================
# 1. State writes inside the sub-agent do NOT leak to parent
# ====================================================================
section("1. Child state writes don't leak to parent")

def _write_lots(ctx):
    ctx.state["i_am_a_leak"] = True
    ctx.state["secret"] = 42
    ctx.state.setdefault("filters", {})["beds_min"] = 99

child = _make_stub_child(on_run=_write_lots)
tool = _agent_as_tool(child)

parent_ctx = _new_parent_ctx(tenant_id="tnt_a",
                             user_query="busco depa",
                             filters={"beds_min": 2})
parent_state_before = dict(parent_ctx.state)
asyncio.run(tool.handler({"message": "do work"}, parent_ctx))

expect("parent.state unchanged after child writes",
       {k: v for k, v in parent_ctx.state.items()
        if k not in ("focus_listing_id", "focus_document_id")}
       == parent_state_before,
       f"diff={set(parent_ctx.state) ^ set(parent_state_before)}")
expect("no 'i_am_a_leak' in parent state",
       "i_am_a_leak" not in parent_ctx.state,
       str(parent_ctx.state))
expect("no 'secret' in parent state",
       "secret" not in parent_ctx.state)
expect("filters in parent state still has only beds_min=2",
       parent_ctx.state.get("filters") == {"beds_min": 2},
       str(parent_ctx.state.get("filters")))


# ====================================================================
# 2. Parent's trace records ONE summary, not raw child events
# ====================================================================
section("2. Parent trace summarized, not full")

def _emit_a_bunch(ctx):
    # Simulate a busy child that emits many trace events.
    for i in range(5):
        ctx.emit("agent_tool", "stub_child",
                 {"tool": f"toolN_{i}", "args": {"x": i}})
    ctx.emit("policy", "stub_child", {"kind": "spend_caps", "ok": True})

child = _make_stub_child(on_run=_emit_a_bunch,
                         returns={"output": "done", "data": None})
tool = _agent_as_tool(child)

parent_ctx = _new_parent_ctx()
asyncio.run(tool.handler({"message": "run"}, parent_ctx))

# The child emitted 5 + 1 + agent_start + agent_end + (possible text_delta
# from the streaming guard for depth==0... but child depth becomes 1 so
# no streaming). Let's just check the parent's trace.
summary_steps = [s for s in parent_ctx.trace
                 if s.kind == "subagent_run"]
expect("parent.trace has exactly ONE subagent_run step",
       len(summary_steps) == 1,
       f"got {len(summary_steps)}: {[s.kind for s in parent_ctx.trace]}")
expect("parent.trace has NO raw agent_tool events from child",
       all(s.kind != "agent_tool"
           or s.agent != "stub_child"
           for s in parent_ctx.trace),
       "")
expect("subagent_run summary carries output_preview",
       isinstance(summary_steps[0].detail.get("output_preview"), str)
       and "done" in summary_steps[0].detail["output_preview"])
expect("subagent_run summary records sub_trace_count > 0",
       summary_steps[0].detail.get("sub_trace_count", 0) >= 5)


# ====================================================================
# 3. Explicit state_delta propagation
# ====================================================================
section("3. state_delta propagates only the named keys")

def _emit_nothing(ctx):
    # Also write something to ctx.state that should NOT propagate.
    ctx.state["leak_me"] = "should not appear in parent"

child = _make_stub_child(
    on_run=_emit_nothing,
    returns={
        "output": "ok",
        "data": None,
        "state_delta": {
            "last_audit_findings": ["hipoteca_vigente"],
            "audit_score": 0.81,
        },
    },
)
tool = _agent_as_tool(child)
parent_ctx = _new_parent_ctx(tenant_id="tnt_a")
asyncio.run(tool.handler({"message": "audit"}, parent_ctx))

expect("state_delta keys reach parent",
       parent_ctx.state.get("last_audit_findings") == ["hipoteca_vigente"]
       and parent_ctx.state.get("audit_score") == 0.81)
expect("non-state_delta writes still don't leak",
       "leak_me" not in parent_ctx.state,
       str(parent_ctx.state))

# Summary detail records what was propagated
summary = [s for s in parent_ctx.trace
           if s.kind == "subagent_run"][-1]
expect("subagent_run.detail.state_delta_keys lists merged keys",
       sorted(summary.detail.get("state_delta_keys") or [])
       == ["audit_score", "last_audit_findings"],
       str(summary.detail))


# ====================================================================
# 4. Parent-side pre-call writes DO flow down (focus pins)
# ====================================================================
section("4. Parent-side focus pins flow into the child via deepcopy")

seen_by_child: dict = {}

def _capture_state(ctx):
    seen_by_child["focus_listing_id"] = ctx.state.get("focus_listing_id")
    seen_by_child["focus_document_id"] = ctx.state.get("focus_document_id")
    seen_by_child["tenant_id"] = ctx.state.get("tenant_id")
    seen_by_child["user_query"] = ctx.state.get("user_query")

child = _make_stub_child(on_run=_capture_state)
tool = _agent_as_tool(child)
parent_ctx = _new_parent_ctx(tenant_id="tnt_x",
                             user_query="hola",
                             filters={"beds_min": 3})
asyncio.run(tool.handler({
    "message": "do",
    "focus_listing_id": "L-001",
    "focus_document_id": "D-009",
}, parent_ctx))

expect("child saw focus_listing_id",
       seen_by_child["focus_listing_id"] == "L-001")
expect("child saw focus_document_id",
       seen_by_child["focus_document_id"] == "D-009")
expect("child saw tenant_id (came from parent)",
       seen_by_child["tenant_id"] == "tnt_x")
expect("child saw user_query (came from parent)",
       seen_by_child["user_query"] == "hola")
expect("parent state now also has the focus pins set",
       parent_ctx.state["focus_listing_id"] == "L-001"
       and parent_ctx.state["focus_document_id"] == "D-009")


# ====================================================================
# 5. Deep copy semantics — mutating a nested dict in the child
#    must NOT affect the parent's nested dict
# ====================================================================
section("5. Nested-dict isolation (deep copy, not shallow)")

def _mutate_nested(ctx):
    ctx.state["filters"]["beds_min"] = 999

child = _make_stub_child(on_run=_mutate_nested)
tool = _agent_as_tool(child)
parent_ctx = _new_parent_ctx(filters={"beds_min": 2, "city": "CDMX"})
asyncio.run(tool.handler({"message": "x"}, parent_ctx))

expect("parent filters.beds_min still 2 after child mutated nested",
       parent_ctx.state["filters"]["beds_min"] == 2,
       str(parent_ctx.state["filters"]))
expect("parent filters.city still CDMX",
       parent_ctx.state["filters"]["city"] == "CDMX")


# ====================================================================
# 6. run_orchestrator end-to-end: each call gets its own RunContext
#    and sub-agent state doesn't leak between calls
# ====================================================================
section("6. run_orchestrator: per-call isolation")

# A locator-triggering query causes the simulated supervisor to invoke
# the locator sub-agent. We expect the parent (top-level) RunContext
# returned by the orchestrator to NOT carry locator's internal scratch.
result1 = asyncio.run(run_orchestrator(
    "busco departamento en Roma Norte",
    state={"tenant_id": "tnt_p"},
))
# Subsequent call should not see any state-bleed.
result2 = asyncio.run(run_orchestrator(
    "hola",
    state={"tenant_id": "tnt_p"},
))
expect("run 1 final state has NO 'i_am_a_leak'",
       "i_am_a_leak" not in result1.get("state", {}))
expect("run 2 final state has NO 'i_am_a_leak'",
       "i_am_a_leak" not in result2.get("state", {}))
# The trace returned to the SSE consumer should contain a subagent_run
# step for at least one call (the locator-triggered run).
trace1 = result1.get("trace", [])
sub_runs_1 = [t for t in trace1 if t["kind"] == "subagent_run"]
expect("locator-triggered run records a subagent_run summary",
       len(sub_runs_1) >= 1,
       str([t['kind'] for t in trace1]))


# ====================================================================
# 7. realestate.py imports + symbol surface
# ====================================================================
section("7. realestate.py wiring guard")

re_src = (SVC / "agents" / "realestate.py").read_text(encoding="utf-8")
expect("realestate.py imports copy",
       "import copy" in re_src)
expect("realestate.py uses copy.deepcopy on state",
       "copy.deepcopy(ctx.state)" in re_src)
expect("realestate.py gives child trace=[]",
       "trace=[]" in re_src)
expect("realestate.py builds a subagent_run TraceStep",
       "subagent_run" in re_src and "TraceStep(" in re_src)
expect("realestate.py merges state_delta when provided",
       "state_delta" in re_src
       and "ctx.state.update(" in re_src)


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
print("  All P5.3 isolation assertions green. ✅")
sys.exit(0)
