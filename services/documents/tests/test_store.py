"""
Documents — store + DSAR contract (Phase 5.1).

Hermetic. No pytest. Exercises the Protocol against
InMemoryDocumentsStore with the CFDI 5-year retention contract.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent

sys.path.insert(0, str(SVC))

from store import InMemoryDocumentsStore, RETENTION_SECONDS    # noqa: E402


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


_counter = [0]
def _ids():
    _counter[0] += 1
    return f"x{_counter[0]:03d}"


# ====================================================================
# 1. Create + sign lifecycle
# ====================================================================
section("1. Document lifecycle")

now = [1700000000.0]
s = InMemoryDocumentsStore(now_fn=lambda: now[0], id_fn=_ids)

d = asyncio.run(s.create_document(
    tenant_id="tnt_x", buyer_id="u_a",
    listing_id="L-1", s3_key="s3://bucket/doc_1.pdf",
    sha256="abc123",
))
expect("create_document → Document",          d.id.startswith("doc_"))
expect("default status = draft",              d.status == "draft")
expect("retention_until is None for draft",   d.retention_until is None)

# Buyer signs
d2 = asyncio.run(s.attach_signer(
    doc_id=d.id, role="buyer", signer_id="u_a",
    signature_hash="hash_buyer",
))
expect("after buyer signs: partially_signed", d2.status == "partially_signed")
expect("still no retention for partial",      d2.retention_until is None)

# Owner signs → fully signed → retention clock starts
d3 = asyncio.run(s.attach_signer(
    doc_id=d.id, role="owner", signer_id="u_owner",
    signature_hash="hash_owner",
))
expect("after both sign: signed",             d3.status == "signed")
expect("retention_until set on signed",       d3.retention_until is not None)
expect("retention is +5 years from signed_at",
       abs(d3.retention_until - d3.signed_at - RETENTION_SECONDS) < 1.0)


# ====================================================================
# 2. is_under_legal_hold
# ====================================================================
section("2. legal-hold predicate")

# Within hold
expect("inside hold = True",                  d3.is_under_legal_hold(now=now[0]))
# After hold expires
expect("after hold = False",
       not d3.is_under_legal_hold(now=now[0] + RETENTION_SECONDS + 10))


# ====================================================================
# 3. DSAR gather — includes drafts + signed
# ====================================================================
section("3. DSAR gather")

g = asyncio.run(s.gather_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("gather has documents key",            "documents" in g)
expect("gather summary mentions retention",
       "CFDI" in g["summary"]["retention_note"])
expect("gather sees the signed doc",          g["summary"]["document_count"] == 1)


# ====================================================================
# 4. DSAR purge — drafts dropped, signed redacted
# ====================================================================
section("4. DSAR purge — drafts dropped, signed redacted (within hold)")

# Add an unsigned draft and verify it gets dropped on purge.
draft = asyncio.run(s.create_document(
    tenant_id="tnt_x", buyer_id="u_a", listing_id="L-2",
))
expect("draft created",                        draft.status == "draft")

res = asyncio.run(s.purge_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("purge.count == 2 (1 draft + 1 signed)", res["count"] == 2,
       str(res))
expect("purge.detail.drafts_dropped == 1",
       res["detail"]["drafts_dropped"] == 1)
expect("purge.detail.signed_redacted == 1",
       res["detail"]["signed_redacted"] == 1)
expect("purge.detail mentions CFDI retention",
       "CFDI" in res["detail"]["retention_note"])

# The signed record should still exist but with PII nulled.
after = asyncio.run(s.list_documents(tenant_id="tnt_x"))
signed_after = [x for x in after if x.status == "signed"]
expect("signed record retained (1 left)",     len(signed_after) == 1)
expect("buyer_id was redacted",
       signed_after[0].buyer_id == "redacted-by-dsar")
expect("signers were redacted",
       all(v == {"redacted": True}
           for v in signed_after[0].signers.values()))
expect("redacted_at set",                     signed_after[0].redacted_at is not None)


# ====================================================================
# 5. Purge after retention expired = real DELETE
# ====================================================================
section("5. Purge after retention expired = hard delete")

now[0] += RETENTION_SECONDS + 10

s5 = InMemoryDocumentsStore(now_fn=lambda: 1700000000.0, id_fn=_ids)
d = asyncio.run(s5.create_document(tenant_id="tnt_x", buyer_id="u_a"))
asyncio.run(s5.attach_signer(doc_id=d.id, role="buyer",
                             signer_id="u_a",
                             signature_hash="x"))
asyncio.run(s5.attach_signer(doc_id=d.id, role="owner",
                             signer_id="u_o",
                             signature_hash="x"))

# Move time well past retention.
s5._now = lambda: 1700000000.0 + RETENTION_SECONDS + 1
res = asyncio.run(s5.purge_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("after hold expires, signed dropped (not redacted)",
       res["detail"]["drafts_dropped"] == 1
       and res["detail"]["signed_redacted"] == 0,
       str(res))


# ====================================================================
# 6. Cross-tenant isolation
# ====================================================================
section("6. Cross-tenant isolation")

s6 = InMemoryDocumentsStore(id_fn=_ids)
asyncio.run(s6.create_document(tenant_id="tnt_a", buyer_id="u_1"))
asyncio.run(s6.create_document(tenant_id="tnt_b", buyer_id="u_1"))
asyncio.run(s6.purge_user_data(tenant_id="tnt_a", user_id="u_1"))
in_a = asyncio.run(s6.list_documents(tenant_id="tnt_a", buyer_id="u_1"))
in_b = asyncio.run(s6.list_documents(tenant_id="tnt_b", buyer_id="u_1"))
expect("tnt_a empty after its purge",         len(in_a) == 0)
expect("tnt_b untouched",                     len(in_b) == 1)


# ====================================================================
# 7. main.py wiring guard
# ====================================================================
section("7. main.py wiring guard")

main_src = (SVC / "main.py").read_text(encoding="utf-8")
for needle in ("build_default_store", "set_documents_store",
               "_docs_store", "gather_user_data", "purge_user_data"):
    expect(f"documents/main.py uses {needle}", needle in main_src, "")
expect("documents/main.py mount_dsar uses Protocol-driven path",
       "_docs_store.purge_user_data" in main_src, "")


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
print("  All documents store assertions green. ✅")
sys.exit(0)
