"""
P4.9 — Listings user-store (favorites + saved searches) +
       migrations sanity + Protocol-driven DSAR wiring.

Sections:
  1. InMemoryListingsUserStore — favorites lifecycle (add / remove /
     list / dedupe)
  2. InMemoryListingsUserStore — saved searches lifecycle
  3. gather_user_data + purge_user_data shape
  4. Cross-tenant + cross-user isolation
  5. Migration SQL parses + contains the expected anchors for every
     P4.9 service

Standalone runner. No pytest.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent
SERVICES = SVC.parent
ROOT = SERVICES.parent

sys.path.insert(0, str(SVC))

from store import (                                           # noqa: E402
    InMemoryListingsUserStore, Favorite, SavedSearch,
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


# Deterministic id + clock for reproducible records.
_counter = [0]
def _ids():
    _counter[0] += 1
    return f"x{_counter[0]:03d}"


# ====================================================================
# 1. Favorites lifecycle
# ====================================================================
section("1. Favorites — add / list / remove / dedupe")

s = InMemoryListingsUserStore(now_fn=lambda: 1700000000.0, id_fn=_ids)

f1 = asyncio.run(s.add_favorite(
    tenant_id="tnt_x", user_id="u_a",
    listing_id="L-1", note="zona tranquila",
))
expect("add_favorite returns Favorite",       isinstance(f1, Favorite))
expect("fav id has fav_ prefix",              f1.id.startswith("fav_"))
expect("fav stored note",                     f1.note == "zona tranquila")
expect("fav created_at pinned",               f1.created_at == 1700000000.0)

# Add a second favorite for the same user
f2 = asyncio.run(s.add_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-2",
))
expect("second favorite has different id",    f1.id != f2.id)

# Duplicate listing → returns same row (uniqueness)
dup = asyncio.run(s.add_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-1",
    note="updated",
))
expect("duplicate returns same id",           dup.id == f1.id)
expect("duplicate updates the note",          dup.note == "updated")

# List
favs = asyncio.run(s.list_favorites(tenant_id="tnt_x", user_id="u_a"))
expect("list returns 2 favorites",            len(favs) == 2)
expect("list sorted by created_at desc",
       favs[0].created_at >= favs[-1].created_at)

# Remove
n = asyncio.run(s.remove_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-1",
))
expect("remove_favorite returns 1",           n == 1)
favs2 = asyncio.run(s.list_favorites(tenant_id="tnt_x", user_id="u_a"))
expect("list shows 1 after remove",           len(favs2) == 1
       and favs2[0].listing_id == "L-2")

# Re-add — gets a fresh id
re_f = asyncio.run(s.add_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-1",
))
expect("re-add after remove gets fresh id",   re_f.id != f1.id)

# Remove non-existent → 0
n = asyncio.run(s.remove_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-NOPE",
))
expect("remove non-existent → 0",             n == 0)


# ====================================================================
# 2. Saved searches lifecycle
# ====================================================================
section("2. Saved searches — add / list / touch")

s2 = InMemoryListingsUserStore(now_fn=lambda: 1700000100.0, id_fn=_ids)

ss1 = asyncio.run(s2.add_search(
    tenant_id="tnt_x", user_id="u_a",
    label="Roma 2br",
    query_text="depa 2 recámaras en Roma Norte hasta 6M",
    filters={"city": "CDMX", "beds_min": 2,
             "price_max_mxn": 6_000_000, "zona": "Roma Norte"},
))
expect("add_search returns SavedSearch",      isinstance(ss1, SavedSearch))
expect("ss id has srch_ prefix",              ss1.id.startswith("srch_"))
expect("ss filters preserved",
       ss1.filters["beds_min"] == 2 and ss1.filters["city"] == "CDMX")

ss2 = asyncio.run(s2.add_search(
    tenant_id="tnt_x", user_id="u_a",
    label="", query_text="Cuernavaca casa con alberca",
    filters={"city": "Cuernavaca", "feature": "alberca"},
))
ss_list = asyncio.run(s2.list_searches(tenant_id="tnt_x", user_id="u_a"))
expect("list_searches returns 2",             len(ss_list) == 2)

asyncio.run(s2.touch_search(search_id=ss1.id))
expect("touch_search updates last_run_at",
       s2._searches[ss1.id].last_run_at == 1700000100.0)  # noqa: SLF001


# ====================================================================
# 3. DSAR gather + purge
# ====================================================================
section("3. DSAR gather_user_data + purge_user_data")

# Reuse s2's data
gathered = asyncio.run(s2.gather_user_data(
    tenant_id="tnt_x", user_id="u_a",
))
expect("gather has favorites + saved_searches keys",
       "favorites" in gathered and "saved_searches" in gathered)
expect("gather summary counts",
       gathered["summary"]["saved_search_count"] == 2
       and gathered["summary"]["favorite_count"] == 0)

# Add a favorite + verify it shows up
asyncio.run(s2.add_favorite(
    tenant_id="tnt_x", user_id="u_a", listing_id="L-DSAR",
))
gathered = asyncio.run(s2.gather_user_data(
    tenant_id="tnt_x", user_id="u_a",
))
expect("gather sees new favorite",
       gathered["summary"]["favorite_count"] == 1)

# Purge
purge_result = asyncio.run(s2.purge_user_data(
    tenant_id="tnt_x", user_id="u_a",
))
expect("purge returns count = 3",             purge_result["count"] == 3,
       str(purge_result))
expect("purge.deleted is True",               purge_result["deleted"] is True)
expect("purge detail breaks out f + s",
       purge_result["detail"]["favorites_removed"] == 1
       and purge_result["detail"]["saved_searches_removed"] == 2)

# Re-gather: empty
gathered = asyncio.run(s2.gather_user_data(
    tenant_id="tnt_x", user_id="u_a",
))
expect("post-purge gather is empty",
       gathered["summary"]["favorite_count"] == 0
       and gathered["summary"]["saved_search_count"] == 0)


# ====================================================================
# 4. Cross-tenant + cross-user isolation
# ====================================================================
section("4. Cross-tenant + cross-user isolation")

s3 = InMemoryListingsUserStore(id_fn=_ids)

# Same listing, two users, two tenants
asyncio.run(s3.add_favorite(tenant_id="tnt_a", user_id="u_1",
                            listing_id="L-shared"))
asyncio.run(s3.add_favorite(tenant_id="tnt_a", user_id="u_2",
                            listing_id="L-shared"))
asyncio.run(s3.add_favorite(tenant_id="tnt_b", user_id="u_1",
                            listing_id="L-shared"))

u1a = asyncio.run(s3.list_favorites(tenant_id="tnt_a", user_id="u_1"))
u2a = asyncio.run(s3.list_favorites(tenant_id="tnt_a", user_id="u_2"))
u1b = asyncio.run(s3.list_favorites(tenant_id="tnt_b", user_id="u_1"))

expect("each (tenant,user) sees one favorite",
       len(u1a) == 1 and len(u2a) == 1 and len(u1b) == 1)
expect("favorites have distinct ids",
       len({u1a[0].id, u2a[0].id, u1b[0].id}) == 3)

# Purging u_1 in tnt_a doesn't touch u_2 or tnt_b
asyncio.run(s3.purge_user_data(tenant_id="tnt_a", user_id="u_1"))
expect("post-purge u_1@tnt_a is empty",
       len(asyncio.run(s3.list_favorites(tenant_id="tnt_a",
                                         user_id="u_1"))) == 0)
expect("u_2@tnt_a untouched",
       len(asyncio.run(s3.list_favorites(tenant_id="tnt_a",
                                         user_id="u_2"))) == 1)
expect("u_1@tnt_b untouched",
       len(asyncio.run(s3.list_favorites(tenant_id="tnt_b",
                                         user_id="u_1"))) == 1)


# ====================================================================
# 5. Migration SQL — every P4.9 service has migrations/0001_init.sql
#    with the documented schema anchors.
# ====================================================================
section("5. Migration SQL — schema anchors per service")

SERVICE_ANCHORS = {
    "listings": {
        "file": "0001_init.sql",
        "must_have": [
            "CREATE TABLE", "favorites", "saved_searches",
            "tenant_id", "user_id", "listing_id",
            "filters", "JSONB", "ROW LEVEL SECURITY",
            "tenant_scope",
        ],
    },
    "scheduling": {
        "file": "0001_init.sql",
        "must_have": [
            "visits", "tenant_id", "buyer_id", "listing_id",
            "proposed_slots", "selected_slot", "deleted_at",
            "purge_deleted_visits", "ROW LEVEL SECURITY",
        ],
    },
    "documents": {
        "file": "0001_init.sql",
        "must_have": [
            "documents", "tenant_id", "buyer_id", "kind",
            "retention_until", "redacted_at",
            "signers", "JSONB",
            "documents_set_retention", "5 years",
        ],
    },
    "payments": {
        "file": "0001_init.sql",
        "must_have": [
            "payments", "cfdis", "tenant_id",
            "payer_id", "payee_id", "method",
            "amount_mxn", "NUMERIC",
            "uuid_fiscal", "rfc_emisor", "rfc_receptor",
            "retention_until", "redacted_at",
            "payments_set_retention", "5 years",
        ],
    },
}

for svc, spec in SERVICE_ANCHORS.items():
    p = SERVICES / svc / "migrations" / spec["file"]
    ok = p.is_file()
    expect(f"{svc}/migrations/{spec['file']} present", ok, str(p))
    if not ok:
        continue
    src = p.read_text(encoding="utf-8")
    for anchor in spec["must_have"]:
        expect(f"{svc} migration mentions '{anchor}'",
               anchor in src, "")


# ====================================================================
# 6. Shared db.py + Protocol surface
# ====================================================================
section("6. _shared/db.py exports + listings store Protocol surface")

DB = SERVICES / "_shared" / "db.py"
ok = DB.is_file()
expect("_shared/db.py present", ok)
if ok:
    s = DB.read_text(encoding="utf-8")
    for sym in ("get_pool", "set_pool", "close_pool",
                "with_conn", "run_migrations",
                "SCHEMA_MIGRATIONS_DDL", "StoreLike"):
        expect(f"db.py exposes {sym}", sym in s, "")

# Listings main.py wires the Protocol-driven store
lm = (SVC / "main.py").read_text(encoding="utf-8")
for sym in ("build_default_store",
            "set_user_store", "get_user_store",
            "FavoriteCreate", "SavedSearchCreate",
            "/users/{user_id}/favorites",
            "/users/{user_id}/searches",
            "gather_user_data", "purge_user_data"):
    expect(f"listings main.py uses {sym}", sym in lm, "")


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
print("  All P4.9 store + migrations assertions green. ✅")
sys.exit(0)
