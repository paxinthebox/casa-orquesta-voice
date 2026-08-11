"""
P4.7 — Shared telemetry + prompt cache + RUNBOOK + backup script.

Tests are pure-stdlib; no pytest. Verifies the modules behave under
the assumptions documented in `docs/RUNBOOK.md` and
`docs/SDK_MIGRATION.md`.
"""
from __future__ import annotations

import io
import json
import os
import sys
import contextlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE.parent
SERVICES = SHARED.parent
ROOT = SERVICES.parent

sys.path.insert(0, str(SHARED))

# Ensure no langfuse env so we exercise the no-op path.
for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
    os.environ.pop(k, None)

import telemetry                                              # noqa: E402
import prompt_cache                                           # noqa: E402


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
# 1. Structured logger
# ====================================================================
section("1. telemetry.get_logger — JSON line schema")

buf = io.StringIO()
log = telemetry.get_logger(service="orchestrator")
with contextlib.redirect_stdout(buf):
    log.info("agent.run", tenant_id="tnt_x", user_id="u_y",
             agents=["realestate"], latency_ms=42)
    log.warn("spend.cap_near", tenant_id="tnt_x", used_usd=4.85, cap_usd=5.0)

lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
expect("two JSON lines emitted",              len(lines) == 2,
       f"got {len(lines)}: {lines}")
rec = json.loads(lines[0])
for k in ("ts", "level", "service", "event", "tenant_id",
          "user_id", "agents", "latency_ms"):
    expect(f"log record has {k}",             k in rec, str(rec))
expect("service field is orchestrator",       rec["service"] == "orchestrator")
expect("event is agent.run",                  rec["event"] == "agent.run")
expect("WARN level emitted",                  json.loads(lines[1])["level"] == "WARN")

# Cached logger
log2 = telemetry.get_logger(service="orchestrator")
expect("get_logger cached by service name",   log is log2)


# ====================================================================
# 2. trace_span
# ====================================================================
section("2. telemetry.trace_span — open / record / close emits one line")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    with telemetry.trace_span(
        name="agent.run", tenant_id="tnt_x", user_id="u_y",
        service="orchestrator",
    ) as span:
        span.record_tokens(model="claude-sonnet-4-6",
                           in_tokens=420, out_tokens=110)
        span.set(focus_listing_id="L-001")

lines = [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]
expect("span emits exactly one log line",     len(lines) == 1)
rec = lines[0]
expect("span event is trace.span",            rec["event"] == "trace.span")
expect("span tokens recorded",                rec["tokens_in"] == 420
       and rec["tokens_out"] == 110)
expect("span model recorded",                 rec["model"] == "claude-sonnet-4-6")
expect("span metadata bridged",               rec.get("focus_listing_id") == "L-001")
expect("span latency_ms is int ≥ 0",          isinstance(rec["latency_ms"], int)
       and rec["latency_ms"] >= 0)


# ====================================================================
# 3. record_vendor_cost + ledger summaries
# ====================================================================
section("3. record_vendor_cost + ledger snapshot / summary")

# Reset the ledger to start clean.
telemetry.ledger_snapshot(drain=True)

with contextlib.redirect_stdout(io.StringIO()):
    telemetry.record_vendor_cost(
        tenant_id="tnt_a", vendor="anthropic",
        model="claude-sonnet-4-6",
        in_tokens=1000, out_tokens=200,
        usd=0.006,
    )
    telemetry.record_vendor_cost(
        tenant_id="tnt_a", vendor="deepgram",
        model="nova-2",
        in_tokens=0, out_tokens=0,
        usd=0.0043,
    )
    telemetry.record_vendor_cost(
        tenant_id="tnt_b", vendor="anthropic",
        model="claude-haiku-4-5",
        in_tokens=2000, out_tokens=400,
        usd=0.0024,
    )

snap = telemetry.ledger_snapshot()
expect("ledger has 3 rows",                   len(snap) == 3)
expect("rows have tenant_id + vendor",        all("tenant_id" in r and "vendor" in r
                                                  for r in snap))

summary = telemetry.ledger_summary_by_tenant()
expect("summary has 3 (tenant, vendor) buckets",
       len(summary["by_tenant_vendor"]) == 3)
total = round(summary["totals"]["usd"], 4)
expect("summary totals USD ≈ 0.0127",         abs(total - 0.0127) < 0.0001,
       f"got {total}")

drained = telemetry.ledger_snapshot(drain=True)
expect("drain returns the same 3 rows",       len(drained) == 3)
expect("post-drain snapshot is empty",        len(telemetry.ledger_snapshot()) == 0)


# ====================================================================
# 4. estimate_usd — sanity per vendor
# ====================================================================
section("4. telemetry.estimate_usd — rate card math")

usd = telemetry.estimate_usd("anthropic",
                             model="claude-sonnet-4-6",
                             in_tokens=1000, out_tokens=1000)
expect("Sonnet 1k+1k ≈ $0.018",               abs(usd - 0.018) < 0.0001,
       f"got {usd}")

usd = telemetry.estimate_usd("anthropic",
                             model="claude-haiku-4-5",
                             in_tokens=1000, out_tokens=1000)
expect("Haiku 1k+1k ≈ $0.0048",               abs(usd - 0.0048) < 0.0001,
       f"got {usd}")

usd = telemetry.estimate_usd("deepgram", model="nova-2", audio_minutes=10)
expect("Deepgram 10 min ≈ $0.043",            abs(usd - 0.043) < 0.0001,
       f"got {usd}")

usd = telemetry.estimate_usd("elevenlabs", model="flash_v2_5", chars=10000)
expect("ElevenLabs 10k chars ≈ $3.00",        abs(usd - 3.0) < 0.0001,
       f"got {usd}")

usd = telemetry.estimate_usd("azure", model="es-MX-DaliaNeural", chars=10000)
expect("Azure 10k chars ≈ $0.16",             abs(usd - 0.16) < 0.0001,
       f"got {usd}")

usd = telemetry.estimate_usd("unknown_vendor")
expect("unknown vendor returns 0.0",          usd == 0.0)


# ====================================================================
# 5. prompt_cache.mark_cacheable + system_blocks
# ====================================================================
section("5. prompt_cache.mark_cacheable + build_request shape")

block = prompt_cache.mark_cacheable({"type": "text", "text": "hello"})
expect("mark_cacheable adds cache_control ephemeral",
       block.get("cache_control") == {"type": "ephemeral"}
       and block["text"] == "hello")

original = {"type": "text", "text": "hi"}
prompt_cache.mark_cacheable(original)
expect("mark_cacheable does not mutate the original",
       "cache_control" not in original)

sys_arr = prompt_cache.system_blocks(
    system_prefix="SYS PROMPT", tools_prefix="TOOLS DEFINITIONS", cache=True,
)
expect("system_blocks returns 2 blocks",      len(sys_arr) == 2)
expect("both system blocks are cached",
       all(b.get("cache_control", {}).get("type") == "ephemeral" for b in sys_arr))

sys_arr_nc = prompt_cache.system_blocks(
    system_prefix="X", tools_prefix=None, cache=False,
)
expect("cache=False produces no cache_control",
       len(sys_arr_nc) == 1
       and "cache_control" not in sys_arr_nc[0])


req = prompt_cache.build_request(
    system_prefix="SYS",
    tools_prefix="TOOLS",
    history=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hola"},
    ],
    user_message="busco depa",
    cache=True,
)
expect("build_request returns {system, messages}",
       set(req.keys()) == {"system", "messages"})
expect("messages preserves history + adds current turn",
       len(req["messages"]) == 3
       and req["messages"][-1] == {"role": "user", "content": "busco depa"})
expect("last user message NOT cached",
       "cache_control" not in req["messages"][-1])
expect("system blocks are cached",
       all(b.get("cache_control", {}).get("type") == "ephemeral"
           for b in req["system"]))


# ====================================================================
# 6. record_cache_usage — telemetry integration
# ====================================================================
section("6. prompt_cache.record_cache_usage — billed-token math")

telemetry.ledger_snapshot(drain=True)
with contextlib.redirect_stdout(io.StringIO()):
    rec = prompt_cache.record_cache_usage(
        tenant_id="tnt_x",
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 2000,
            "output_tokens": 120,
        },
        request_id="req_test_1",
        service="orchestrator",
    )
expect("rec carries cache_read tokens",       rec.cache_read_input_tokens == 2000)
expect("rec carries output tokens",           rec.output_tokens == 120)

snap = telemetry.ledger_snapshot()
expect("usage logged to ledger",              len(snap) == 1)
row = snap[0]
expect("billed_in ≈ 50 + (2000 * 0.10)",      row["in_tokens"] == 250,
       f"got {row['in_tokens']}")
expect("output tokens passthrough",           row["out_tokens"] == 120)
expect("vendor is anthropic",                 row["vendor"] == "anthropic")


# ====================================================================
# 7. summarize_cache_hit_ratio
# ====================================================================
section("7. summarize_cache_hit_ratio")

recs = [
    prompt_cache.CacheUsageRecord(
        tenant_id="tnt_x", model="claude-sonnet-4-6",
        regular_input_tokens=100, cache_creation_input_tokens=1500,
        cache_read_input_tokens=0, output_tokens=200,
    ),
    prompt_cache.CacheUsageRecord(
        tenant_id="tnt_x", model="claude-sonnet-4-6",
        regular_input_tokens=50, cache_creation_input_tokens=0,
        cache_read_input_tokens=1500, output_tokens=180,
    ),
    prompt_cache.CacheUsageRecord(
        tenant_id="tnt_x", model="claude-sonnet-4-6",
        regular_input_tokens=40, cache_creation_input_tokens=0,
        cache_read_input_tokens=1500, output_tokens=170,
    ),
]
s = prompt_cache.summarize_cache_hit_ratio(recs)
expect("total input tokens = 4690",           s["total_input_tokens"] == 4690,
       str(s))
expect("cache_hit_ratio between 0 and 1",     0.0 <= s["cache_hit_ratio"] <= 1.0)
expect("savings_tokens_vs_nocache > 0",       s["savings_tokens_vs_nocache"] > 0)
expect("output_tokens summed",                s["output_tokens"] == 550)


# ====================================================================
# 8. RUNBOOK structure + backup script + SDK doc
# ====================================================================
section("8. RUNBOOK + backup script + SDK_MIGRATION sanity")

runbook = ROOT / "docs" / "RUNBOOK.md"
expect("docs/RUNBOOK.md present",             runbook.is_file())
if runbook.is_file():
    s = runbook.read_text(encoding="utf-8")
    for marker in (
        "Quick reference card",
        "Service inventory",
        "Dashboards + links",
        "Common incidents",
        "Deploy + rollback",
        "Database recovery",
        "Secrets + rotation",
        "Escalation tree",
        "/dsar/user/{user_id}",
        "audit chain",
    ):
        ok2 = marker in s
        expect(f"RUNBOOK has '{marker}'", ok2)

backup = ROOT / "scripts" / "backup_postgres.sh"
expect("scripts/backup_postgres.sh present",  backup.is_file())
if backup.is_file():
    b = backup.read_text(encoding="utf-8")
    for marker in ("pg_dump", "gpg", "aws s3",
                   "--dry-run", "--verify-latest",
                   "BACKUP_GPG_RECIPIENT_KEY", "S3_BUCKET"):
        expect(f"backup_postgres.sh mentions {marker}", marker in b)
    expect("backup_postgres.sh is executable",
           os.access(str(backup), os.X_OK))

sdkdoc = ROOT / "docs" / "SDK_MIGRATION.md"
expect("docs/SDK_MIGRATION.md present",       sdkdoc.is_file())
if sdkdoc.is_file():
    s = sdkdoc.read_text(encoding="utf-8")
    for marker in ("TL;DR", "Migration plan",
                   "Token-cost expectations",
                   "Change log",
                   "Prompt-prefix caching",
                   "Per-tenant cost ledger"):
        expect(f"SDK_MIGRATION has '{marker}'", marker in s)


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
print("  All P4.7 ops-hardening assertions green. ✅")
sys.exit(0)
