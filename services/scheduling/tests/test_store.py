"""
Scheduling — store + DSAR contract (Phase 5.1).

Hermetic. No pytest. Exercises the Protocol against InMemoryVisitsStore.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent
SERVICES = SVC.parent

sys.path.insert(0, str(SVC))

from store import InMemoryVisitsStore                         # noqa: E402


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


# Deterministic ids + clock
_counter = [0]
def _ids():
    _counter[0] += 1
    return f"x{_counter[0]:03d}"


# ====================================================================
# 1. Lifecycle: create / confirm / cancel / feedback
# ====================================================================
section("1. Visit lifecycle")

s = InMemoryVisitsStore(now_fn=lambda: 1700000000.0, id_fn=_ids)

v = asyncio.run(s.create_visit(
    tenant_id="tnt_x", listing_id="L-1", buyer_id="u_a",
    proposed_slots=[{"start_iso": "2026-06-12T10:00:00Z",
                     "end_iso":   "2026-06-12T11:00:00Z"}],
))
expect("create_visit returns Visit",          v is not None
       and v.id.startswith("V-"))
expect("default status is 'proposed'",        v.status == "proposed")

v2 = asyncio.run(s.confirm_visit(
    visit_id=v.id,
    selected_slot=v.proposed_slots[0],
))
expect("confirm_visit flips status",          v2.status == "confirmed"
       and v2.selected_slot is not None)

v3 = asyncio.run(s.add_feedback(
    visit_id=v.id, feedback={"rating": 5, "notes": "muy bien"},
))
expect("add_feedback flips status to completed",
       v3.status == "completed" and v3.feedback["rating"] == 5)

v4 = asyncio.run(s.create_visit(
    tenant_id="tnt_x", listing_id="L-2", buyer_id="u_a",
    proposed_slots=[],
))
v5 = asyncio.run(s.cancel_visit(visit_id=v4.id))
expect("cancel_visit flips status",           v5.status == "cancelled")

# Missing ids return None
expect("get_visit on missing → None",         asyncio.run(s.get_visit(
    visit_id="V-NOPE")) is None)
expect("confirm on missing → None",
       asyncio.run(s.confirm_visit(visit_id="V-NOPE",
                                   selected_slot={})) is None)


# ====================================================================
# 2. List filters
# ====================================================================
section("2. list_visits filters")

s2 = InMemoryVisitsStore(id_fn=_ids)
for i in range(3):
    asyncio.run(s2.create_visit(
        tenant_id="tnt_x", listing_id=f"L-{i}", buyer_id="u_a",
        proposed_slots=[],
    ))
asyncio.run(s2.create_visit(
    tenant_id="tnt_x", listing_id="L-z", buyer_id="u_b",
    proposed_slots=[],
))
asyncio.run(s2.create_visit(
    tenant_id="tnt_y", listing_id="L-z", buyer_id="u_a",
    proposed_slots=[],
))

all_x = asyncio.run(s2.list_visits(tenant_id="tnt_x"))
expect("tenant filter: 4 visits in tnt_x",    len(all_x) == 4)

ua_in_x = asyncio.run(s2.list_visits(tenant_id="tnt_x", buyer_id="u_a"))
expect("buyer filter: 3 for u_a in tnt_x",    len(ua_in_x) == 3)

ua_in_y = asyncio.run(s2.list_visits(tenant_id="tnt_y", buyer_id="u_a"))
expect("buyer u_a in tnt_y → 1",              len(ua_in_y) == 1)

# Status filter
v = asyncio.run(s2.create_visit(
    tenant_id="tnt_x", listing_id="L-cancel", buyer_id="u_a",
    proposed_slots=[],
))
asyncio.run(s2.cancel_visit(visit_id=v.id))
cancelled = asyncio.run(s2.list_visits(
    tenant_id="tnt_x", status="cancelled"))
expect("status filter: 1 cancelled in tnt_x", len(cancelled) == 1)


# ====================================================================
# 3. DSAR gather / purge
# ====================================================================
section("3. DSAR gather + purge (hard-delete; no fiscal hold)")

s3 = InMemoryVisitsStore(id_fn=_ids)
asyncio.run(s3.create_visit(tenant_id="tnt_x", listing_id="L-1",
                            buyer_id="u_a", proposed_slots=[]))
asyncio.run(s3.create_visit(tenant_id="tnt_x", listing_id="L-2",
                            buyer_id="u_a", proposed_slots=[]))
asyncio.run(s3.create_visit(tenant_id="tnt_x", listing_id="L-3",
                            buyer_id="u_b", proposed_slots=[]))

g = asyncio.run(s3.gather_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("gather has visits + summary",         "visits" in g
       and g["summary"]["visit_count"] == 2)

# Tenant isolation in gather
g_other = asyncio.run(s3.gather_user_data(
    tenant_id="tnt_other", user_id="u_a",
))
expect("gather is tenant-scoped",             g_other["summary"]["visit_count"] == 0)

# Purge
res = asyncio.run(s3.purge_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("purge.count == 2",                    res["count"] == 2)
expect("purge.deleted is True",               res["deleted"] is True)
# u_b untouched
ub = asyncio.run(s3.list_visits(tenant_id="tnt_x", buyer_id="u_b"))
expect("u_b's visit untouched by u_a's purge", len(ub) == 1)


# ====================================================================
# 4. Cross-tenant isolation
# ====================================================================
section("4. Cross-tenant isolation")

s4 = InMemoryVisitsStore(id_fn=_ids)
asyncio.run(s4.create_visit(tenant_id="tnt_a", listing_id="L-1",
                            buyer_id="u_1", proposed_slots=[]))
asyncio.run(s4.create_visit(tenant_id="tnt_b", listing_id="L-1",
                            buyer_id="u_1", proposed_slots=[]))

# Purge in tnt_a doesn't touch tnt_b's row
asyncio.run(s4.purge_user_data(tenant_id="tnt_a", user_id="u_1"))
in_a = asyncio.run(s4.list_visits(tenant_id="tnt_a", buyer_id="u_1"))
in_b = asyncio.run(s4.list_visits(tenant_id="tnt_b", buyer_id="u_1"))
expect("tnt_a is empty after its purge",      len(in_a) == 0)
expect("tnt_b is untouched",                  len(in_b) == 1)


# ====================================================================
# 5. Health
# ====================================================================
section("5. health()")

h = asyncio.run(s4.health())
expect("health.backend == 'in-memory'",       h["backend"] == "in-memory")
expect("health.visit_count present",          "visit_count" in h)


# ====================================================================
# 6. main.py wiring guard (Protocol-driven path)
# ====================================================================
section("6. main.py wiring guard")

main_src = (SVC / "main.py").read_text(encoding="utf-8")
for needle in (
    "build_default_store",
    "set_visits_store",
    "_visits_store",
    "purge_user_data",
    "gather_user_data",
):
    expect(f"scheduling/main.py uses {needle}",
           needle in main_src, "")

# The mount_dsar call must reference the new async Protocol-driven path.
expect("scheduling/main.py mount_dsar uses Protocol-driven purge",
       "_scheduling_dsar_purge" in main_src
       and "_visits_store.purge_user_data" in main_src,
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
print("  All scheduling store assertions green. ✅")
sys.exit(0)
