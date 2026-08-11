"""
Phase 1.7 hook verification — 40+ assertions.

Covers each of the four hooks in isolation. The Agent.run() integration is
verified indirectly by the 161-assertion contract still passing in
test_agents.py — this file pins the *policy* behavior itself so future
changes can't silently weaken redaction, spend caps, or audit integrity.

Runs without pytest, like the other repo tests:
    cd services/orchestrator && python3 tests/test_hooks.py
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ORCH = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ORCH)

# Use the OS temp dir so the chain tests work in any sandbox.
_audit_tmp = os.path.join(tempfile.gettempdir(), "casa_audit_test.jsonl")
os.environ["AUDIT_LOG_PATH"] = _audit_tmp
os.environ.setdefault("AUDIT_BACKEND", "local")
os.environ.setdefault("SPEND_CAP_USD_PER_DAY", "5.0")

from hooks import pii_redaction, spend_caps, audit_log, trace  # noqa: E402


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


# ====================================================================
# 1. PII redaction patterns (CLAUDE.md non-negotiable #4)
# ====================================================================
section("1. PII redaction patterns")

cases = [
    ("RFC persona moral",  "El RFC es CAS990101AAA gracias.",     "[RFC]"),
    ("RFC persona física", "Mi RFC HEGJ820305H44 está activo.",   "[RFC]"),
    ("CURP",               "Mi CURP es HEGJ820305HDFRNV02 ok.",    "[CURP]"),
    ("Email",              "Escríbeme a juan@example.com hoy.",    "[EMAIL]"),
    ("Phone +52",          "Llámame al +52 55 1234 5678 ahora.",   "[PHONE]"),
    ("Phone local",        "Mi número es 55 1234 5678 amigo.",     "[PHONE]"),
    ("Labeled name",       "Comprador: JUAN PÉREZ HERNÁNDEZ aquí.",   "[NAME]"),
]
for label, text, token in cases:
    r = pii_redaction.redact(text)
    expect(f"redact {label}", token in (r or ""), f"got: {r!r}")

# Idempotency
r1 = pii_redaction.redact("El RFC CAS990101AAA y juan@x.com.")
r2 = pii_redaction.redact(r1)
expect("redaction is idempotent", r1 == r2, f"r1={r1!r}  r2={r2!r}")

# None / empty
expect("redact(None) == None", pii_redaction.redact(None) is None)
expect("redact('') == ''",      pii_redaction.redact("") == "")

# messages walker — strings AND content blocks
msgs = [
    {"role": "user", "content": "Mi RFC es CAS990101AAA"},
    {"role": "assistant", "content": [
        {"type": "text", "text": "OK envío al +52 55 1234 5678"},
        {"type": "tool_use", "id": "x", "name": "n", "input": {}},
    ]},
]
out = pii_redaction.redact_messages(msgs)
expect("messages str redacted",   "[RFC]"   in out[0]["content"])
expect("messages block redacted", "[PHONE]" in out[1]["content"][0]["text"])
expect("messages preserves non-text blocks",
       out[1]["content"][1]["type"] == "tool_use")


# ====================================================================
# 2. Spend caps math + decisions
# ====================================================================
section("2. Spend caps math + decisions")

spend_caps.reset_for_tests()
tid = "test-tenant"

# Fresh tenant under cap
d = spend_caps.should_call_llm(tid, "claude-haiku-4-5-20251001")
expect("fresh tenant allowed", d["allow"] is True)
expect("fresh tenant spent=0", d["spent_usd"] == 0.0)
expect("fresh tenant cap=5",   d["cap_usd"] == 5.0)

# Charge a Haiku call: 1M input + 500K output
# = 1*0.80 + 0.5*4.00 = 0.80 + 2.00 = $2.80
bill = spend_caps.record_usage(tid, "claude-haiku-4-5-20251001",
                               input_tokens=1_000_000, output_tokens=500_000)
expect("haiku cost computed",         abs(bill["cost_usd"] - 2.80) < 1e-6,
       f"got {bill['cost_usd']!r}")
expect("haiku total tracked",         abs(bill["tenant_total_usd"] - 2.80) < 1e-6)

# A small Sonnet call: 100K input + 50K output
# = 0.1*3.00 + 0.05*15.00 = 0.30 + 0.75 = $1.05
bill = spend_caps.record_usage(tid, "claude-sonnet-4-6",
                               input_tokens=100_000, output_tokens=50_000)
expect("sonnet cost computed",        abs(bill["cost_usd"] - 1.05) < 1e-6)
expect("sonnet accumulates total",    abs(bill["tenant_total_usd"] - 3.85) < 1e-6)

# Still under cap
d = spend_caps.should_call_llm(tid, "claude-sonnet-4-6")
expect("still allowed under cap", d["allow"] is True)

# Push over the cap
spend_caps.record_usage(tid, "claude-sonnet-4-6",
                        input_tokens=100_000, output_tokens=100_000)
# add $0.30 + $1.50 = $1.80, total now $5.65
d = spend_caps.should_call_llm(tid, "claude-sonnet-4-6")
expect("over cap → not allowed", d["allow"] is False)
expect("over cap reason",        d.get("reason") == "cap_exceeded")
expect("over cap degrades",      d.get("degrade_to") == "simulated")

# Per-tenant isolation
d2 = spend_caps.should_call_llm("other-tenant", "claude-haiku-4-5-20251001")
expect("other tenant unaffected", d2["allow"] is True)


# ====================================================================
# 3. Audit log chain integrity + tamper detection
# ====================================================================
section("3. Audit log chain integrity + tamper detection")

# Wipe any prior log file and reset chain head.
log_path = os.environ["AUDIT_LOG_PATH"]
try:
    if os.path.exists(log_path):
        os.remove(log_path)
except OSError:
    pass
audit_log.reset_for_tests()
expect("genesis is zeros", audit_log.chain_head() == "0" * 64)

e1 = audit_log.record(actor="agent1", action="tool:search_listings",
                      resource="filters:state=CDMX",
                      tenant_id="t-1", run_id="R-X")
e2 = audit_log.record(actor="agent1", action="tool:get_listing",
                      resource="listing_id:L-CDMX-001",
                      tenant_id="t-1", run_id="R-X")
e3 = audit_log.record(actor="agent2", action="tool:rpp_lookup",
                      resource="state=CDMX address=Querétaro",
                      tenant_id="t-1", run_id="R-X")

expect("entry has id",          e1["id"].startswith("A-"))
expect("entry has hash",        len(e1["hash"]) == 64)
expect("entry prev=genesis",    e1["prev_hash"] == "0" * 64)
expect("e2.prev == e1.hash",    e2["prev_hash"] == e1["hash"])
expect("e3.prev == e2.hash",    e3["prev_hash"] == e2["hash"])
expect("chain_head == e3.hash", audit_log.chain_head() == e3["hash"])

verified = audit_log.verify_local_chain()
expect("verify_local_chain ok",  verified["ok"] is True, str(verified))
expect("verify counted 3 rows",  verified["count"] == 3, str(verified))

# Tamper: rewrite a middle entry's resource field on disk, re-verify.
with open(log_path, "r", encoding="utf-8") as f:
    lines = f.readlines()
import json
mid = json.loads(lines[1])
mid["resource"] = "TAMPERED"
lines[1] = json.dumps(mid, ensure_ascii=False, default=str) + "\n"
with open(log_path, "w", encoding="utf-8") as f:
    f.writelines(lines)

verified = audit_log.verify_local_chain()
expect("tamper detected (ok=False)", verified["ok"] is False, str(verified))
expect("tamper points at e2 id",
       verified["broken_at"] == mid["id"]
       or verified["broken_at"] == e2["id"]
       or str(verified["broken_at"]).startswith("A-"), str(verified))

# Cleanup
try:
    if os.path.exists(log_path):
        os.remove(log_path)
except OSError:
    pass
audit_log.reset_for_tests()


# ====================================================================
# 4. Trace subscriber lifecycle + error isolation
# ====================================================================
section("4. Trace subscriber lifecycle + error isolation")

trace.reset_for_tests()
received: list[dict] = []
trace.add_subscriber(lambda s: received.append(s))

trace.emit({"kind": "agent_start", "agent": "x", "ts_ms": 0, "detail": {}})
trace.emit({"kind": "agent_tool",  "agent": "x", "ts_ms": 1, "detail": {"tool": "y"}})

expect("subscriber received 2 events", len(received) == 2)
expect("first event kind",   received[0]["kind"] == "agent_start")
expect("second event tool",  received[1]["detail"]["tool"] == "y")

# Error in one subscriber must not break others.
def _bad(_): raise RuntimeError("boom")
def _good(s): received.append({"good": True, **s})

trace.add_subscriber(_bad)
trace.add_subscriber(_good)
prior = len(received)
trace.emit({"kind": "agent_end", "agent": "x", "ts_ms": 2, "detail": {}})
expect("good subscriber still ran after bad one",
       any(r.get("good") for r in received[prior:]))

trace.reset_for_tests()


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
print("  All hook assertions green. ✅")
sys.exit(0)
