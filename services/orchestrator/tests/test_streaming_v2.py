"""
P5.2 — Streaming partial tokens.

Verifies the new wire contract:
  1. `_split_text_for_streaming` produces multiple deltas whose
     concatenation equals the original text.
  2. `stream_text_through_emit` emits matching `text_delta` events on
     a RunContext.
  3. `run_orchestrator` (simulated path — no Anthropic key required)
     emits a sequence of `text_delta` events for the final reply,
     followed by the supervisor's `agent_end`.
  4. Only the TOP-LEVEL agent (depth 0) streams. Sub-agent text never
     reaches the SSE stream — the supervisor wraps + reframes.
  5. `services/orchestrator/main.py` no longer pushes the synthetic
     post-run text_delta event.

Standalone runner. No pytest. No network.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import types as _types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent
sys.path.insert(0, str(SVC))

# ---------------------------------------------------------------------
# Stub external deps so the agents module can import without PyPI
# (httpx + anthropic). Same pattern as tests/test_agents.py.
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

# Force the simulated path (no real Anthropic key).
os.environ.pop("ANTHROPIC_API_KEY", None)

from agents import (                                         # noqa: E402
    RunContext, _split_text_for_streaming,
    stream_text_through_emit, run_orchestrator,
)


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
# 1. _split_text_for_streaming
# ====================================================================
section("1. _split_text_for_streaming — chunk rules")

cases = [
    "",
    "Hola",
    "Hola mundo",
    "Encontré tres opciones en Roma Norte.",
    ("Encontré tres opciones en Roma Norte. La primera tiene dos "
     "recámaras y cuesta cinco millones. La segunda es más amplia "
     "pero está en otro fraccionamiento."),
    "Una dos tres cuatro cinco seis siete ocho",  # 8 words, no punctuation
    "abcdefghi " * 12,                            # long words → max_chars
    "Sí, claro. Adelante.",
]

for text in cases:
    chunks = _split_text_for_streaming(text)
    joined = "".join(chunks)
    if not text:
        expect("empty text → 0 chunks",         len(chunks) == 0)
        continue
    expect(f"chunks reconstruct text ({len(text)} ch)",
           joined == text,
           f"got {joined!r}")
    if any(p in text for p in ".!?,;:") or len(text.split()) >= 4:
        expect(f"  produces ≥2 chunks (len={len(text)})",
               len(chunks) >= 2,
               f"got {len(chunks)} for {text[:40]!r}")

# Word-count flush: 4 words triggers a flush at the 4th word boundary
chunks = _split_text_for_streaming("alfa beta gamma delta epsilon zeta",
                                   target_words=4)
expect("word-count flush at 4 words",            len(chunks) == 2,
       f"got {len(chunks)}: {chunks}")

# Max-chars flush. The chunker tokenizes on whitespace and won't
# split mid-token (preserving word integrity is important for TTS).
# Use whitespace-separated words to exercise the cap.
big = " ".join(["palabra"] * 30)
chunks = _split_text_for_streaming(big, target_words=999, max_chars=40)
expect("max-chars-or-less words trigger a flush",
       len(chunks) >= 2,
       f"got {len(chunks)} chunks of avg len "
       f"{sum(len(c) for c in chunks) // max(1,len(chunks))}")
expect("max-chars flush keeps chunks reasonable (≤ ~50)",
       max(len(c) for c in chunks) <= 50,
       f"max chunk len = {max(len(c) for c in chunks)}")
expect("max-chars + reconstruction still works",
       "".join(chunks) == big)


# ====================================================================
# 2. stream_text_through_emit on a RunContext
# ====================================================================
section("2. stream_text_through_emit emits matching trace events")

ctx = RunContext(run_id="R-TEST", state={})
stream_text_through_emit(ctx, "realestate_agent",
                         "Hola, encontré tres opciones en Roma Norte.")
deltas = [s for s in ctx.trace if s.kind == "text_delta"]
expect("emitted ≥ 2 text_delta events",          len(deltas) >= 2,
       f"got {len(deltas)}: {[s.detail.get('text') for s in deltas]}")
expect("each delta has agent_name set",
       all(s.agent == "realestate_agent" for s in deltas))
expect("concatenated text matches input",
       "".join(s.detail["text"] for s in deltas)
       == "Hola, encontré tres opciones en Roma Norte.")


# ====================================================================
# 3. run_orchestrator end-to-end (simulated path)
# ====================================================================
section("3. run_orchestrator emits text_delta sequence (simulated path)")

result = asyncio.run(run_orchestrator(
    "Hola, ¿qué puedes hacer?",
    state={"tenant_id": "tnt_pilot_mx"},
))
trace = result["trace"]
deltas = [t for t in trace if t["kind"] == "text_delta"]
agent_end = [t for t in trace if t["kind"] == "agent_end"]

expect("trace contains ≥ 2 text_delta events",
       len(deltas) >= 2,
       f"got {len(deltas)}")
expect("all text_deltas come from realestate_agent (top-level)",
       all(d["agent"] == "realestate_agent" for d in deltas),
       str([d['agent'] for d in deltas]))
expect("concatenated deltas equal final reply",
       "".join(d["detail"]["text"] for d in deltas).strip()
       == result["reply"].strip(),
       f"concat={(''.join(d['detail']['text'] for d in deltas))!r} "
       f"reply={result['reply']!r}")
expect("agent_end fires after the deltas",
       len(agent_end) > 0 and trace.index(agent_end[-1]) > trace.index(deltas[-1]),
       "ordering wrong")


# ====================================================================
# 4. Sub-agent depth gating: locator/audit runs do NOT emit deltas
# ====================================================================
section("4. Sub-agent depth gating (only depth==0 streams)")

# A locator-triggering query causes the simulated supervisor to call
# locator_agent as a sub-agent. The sub-agent's output is wrapped
# ("[Búsqueda] …") by the supervisor; only the wrapped supervisor reply
# should stream.
result = asyncio.run(run_orchestrator(
    "busco departamento en Roma Norte",
    state={"tenant_id": "tnt_pilot_mx"},
))
trace = result["trace"]
deltas = [t for t in trace if t["kind"] == "text_delta"]
expect("sub-agent run produces text_deltas (for the wrapped reply)",
       len(deltas) >= 1)
# All deltas must be from the SUPERVISOR — not the sub-agent.
sub_agent_deltas = [d for d in deltas
                    if d["agent"] in ("locator_agent", "audit_agent")]
expect("no text_delta from sub-agents",          len(sub_agent_deltas) == 0,
       str([d['agent'] for d in sub_agent_deltas]))

# Audit path
result = asyncio.run(run_orchestrator(
    "auditar el contrato de Roma 123",
    state={"tenant_id": "tnt_pilot_mx"},
))
trace = result["trace"]
deltas = [t for t in trace if t["kind"] == "text_delta"]
sub_agent_deltas = [d for d in deltas
                    if d["agent"] in ("locator_agent", "audit_agent")]
expect("audit-routed run: no sub-agent text_delta",
       len(sub_agent_deltas) == 0,
       str([d['agent'] for d in sub_agent_deltas]))


# ====================================================================
# 5. Idempotency: re-streaming the same text wouldn't double-emit
#    (the orchestrator's "already_streamed" guard works)
# ====================================================================
section("5. Idempotency guard: no double-stream when reply was already streamed")

# Simulate the case where the real path already streamed by manually
# emitting a text_delta first, then calling run_orchestrator. The
# guard at the end of run_orchestrator scans for prior text_deltas
# from the supervisor and skips re-streaming.
#
# We can't easily simulate this from outside since run_orchestrator
# constructs its own ctx, but we can verify the inverse: that NO
# text_delta is double-emitted on a normal simulated run.
result = asyncio.run(run_orchestrator(
    "Hola", state={"tenant_id": "tnt_pilot_mx"},
))
trace = result["trace"]
deltas = [t for t in trace if t["kind"] == "text_delta"]
concat = "".join(d["detail"]["text"] for d in deltas)
expect("simulated run: concat ≤ reply.length + 1",
       len(concat) <= len(result["reply"]) + 1,
       f"concat={len(concat)} reply={len(result['reply'])}")


# ====================================================================
# 6. orchestrator/main.py: synthetic text_delta removed
# ====================================================================
section("6. orchestrator/main.py — synthetic text_delta dropped")

main_src = (SVC / "main.py").read_text(encoding="utf-8")
# The old code had this exact comment fragment. P5.2 removed it.
expect("old 'single chunk for now' comment removed",
       "single chunk for now" not in main_src,
       "")
# The new comment block is present.
expect("P5.2 comment about streaming arrival is present",
       "text_delta events now arrive *during* the run" in main_src,
       "")
# There must still be a put_nowait for run_end (we kept that).
expect("run_end is still emitted",
       'put_nowait({\n                "kind": "run_end"' in main_src
       or '"kind": "run_end"' in main_src, "")
# But there should be no put_nowait for kind=text_delta in _runner —
# the only text_delta emission path now goes through ctx.emit() →
# trace_hook.emit() → the _subscriber that already drains into the queue.
# Search just the _runner body (between `async def _runner` and the
# next `async def`).
m_start = main_src.find("async def _runner")
m_end = main_src.find("async def ", m_start + 1)
runner_body = main_src[m_start: m_end if m_end > 0 else len(main_src)]
expect("_runner does NOT push 'text_delta' via put_nowait",
       not re.search(r'put_nowait\([^)]*"kind"\s*:\s*"text_delta"',
                     runner_body, re.S),
       "")


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
print("  All P5.2 streaming assertions green. ✅")
sys.exit(0)
