"""
Payments + CFDI — store + DSAR contract (Phase 5.1).

Hermetic. No pytest. Exercises the Protocol against
InMemoryPaymentsStore with the SAT 5-year retention contract.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SVC = HERE.parent

sys.path.insert(0, str(SVC))

from store import InMemoryPaymentsStore, RETENTION_SECONDS    # noqa: E402


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
# 1. Payment lifecycle: intent → capture → refund
# ====================================================================
section("1. Payment lifecycle + auto-retention on capture")

now = [1700000000.0]
s = InMemoryPaymentsStore(now_fn=lambda: now[0], id_fn=_ids)

p = asyncio.run(s.create_intent(
    tenant_id="tnt_x", payer_id="u_a", payee_id="u_b",
    amount_mxn=12345.67, description="enganche",
))
expect("create_intent returns Payment",        p.id.startswith("pay_"))
expect("intent status",                        p.status == "intent")
expect("no retention on intent",               p.retention_until is None)

p2 = asyncio.run(s.capture_payment(
    payment_id=p.id, provider_intent_id="pi_test123",
))
expect("capture flips status",                 p2.status == "captured")
expect("captured_at set",                      p2.captured_at == now[0])
expect("retention set to +5y",
       abs(p2.retention_until - now[0] - RETENTION_SECONDS) < 1.0)
expect("provider_intent_id captured",
       p2.provider_intent_id == "pi_test123")

p3 = asyncio.run(s.refund_payment(payment_id=p.id))
expect("refund flips status",                  p3.status == "refunded")


# ====================================================================
# 2. CFDI emission — retention is mandatory at insert
# ====================================================================
section("2. CFDI emission")

c = asyncio.run(s.emit_cfdi(
    tenant_id="tnt_x", payment_id=p.id,
    uuid_fiscal="A1B2-C3D4",
    rfc_emisor="ORQ100101AB1", rfc_receptor="ABC900101XY2",
    total_mxn=12345.67,
    xml_blob="<cfdi:Comprobante>…</cfdi:Comprobante>",
    pdf_s3_key="s3://bucket/cfdi.pdf",
))
expect("emit_cfdi returns Cfdi",               c.id.startswith("cfdi_"))
expect("CFDI retention set to +5y at insert",
       abs(c.retention_until - now[0] - RETENTION_SECONDS) < 1.0)
expect("CFDI is under legal hold",
       c.is_under_legal_hold(now=now[0]))


# ====================================================================
# 3. List filters
# ====================================================================
section("3. List filters")

# Add a foreign-tenant payment to verify isolation
asyncio.run(s.create_intent(tenant_id="tnt_y", payer_id="u_a",
                            payee_id=None, amount_mxn=1.0))
mine = asyncio.run(s.list_payments(tenant_id="tnt_x"))
expect("list_payments by tenant",              len(mine) == 1)

mine_by_user = asyncio.run(s.list_payments(
    tenant_id="tnt_x", user_id="u_a"))
expect("list_payments by user",                len(mine_by_user) == 1)

# CFDI filter
cfdis = asyncio.run(s.list_cfdis(tenant_id="tnt_x"))
expect("list_cfdis returns 1",                 len(cfdis) == 1)
cfdis_rfc = asyncio.run(s.list_cfdis(
    tenant_id="tnt_x", rfc_receptor="ABC900101XY2"))
expect("list_cfdis filters by rfc_receptor",   len(cfdis_rfc) == 1)
cfdis_none = asyncio.run(s.list_cfdis(
    tenant_id="tnt_x", rfc_receptor="WRONG"))
expect("rfc filter is exact",                  len(cfdis_none) == 0)


# ====================================================================
# 4. DSAR gather
# ====================================================================
section("4. DSAR gather")

g = asyncio.run(s.gather_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("gather has payments + cfdis keys",
       "payments" in g and "cfdis" in g)
expect("gather sees the captured payment",     g["summary"]["payment_count"] == 1)
expect("gather sees the emitted CFDI",         g["summary"]["cfdi_count"] == 1)
expect("retention_note mentions SAT",
       "SAT" in g["summary"]["retention_note"])


# ====================================================================
# 5. DSAR purge — captured payment redacted + CFDI redacted (within hold)
# ====================================================================
section("5. DSAR purge — captured payment redacted + CFDI redacted")

# Add a fresh INTENT (no hold) so we can verify drops happen.
intent = asyncio.run(s.create_intent(
    tenant_id="tnt_x", payer_id="u_a", payee_id=None,
    amount_mxn=99.0,
))
expect("intent has no retention",              intent.retention_until is None)

res = asyncio.run(s.purge_user_data(tenant_id="tnt_x", user_id="u_a"))
expect("intents_dropped == 1",
       res["detail"]["intents_dropped"] == 1, str(res))
expect("payments_redacted == 1",
       res["detail"]["payments_redacted"] == 1, str(res))
expect("cfdis_redacted == 1",
       res["detail"]["cfdis_redacted"] == 1, str(res))
expect("retention_note mentions SAT/CFDI",
       "SAT" in res["detail"]["retention_note"])

# Captured payment retained with redacted PII.
captured = await_payment = asyncio.run(s.get_payment(payment_id=p.id))
expect("captured payment retained",            captured is not None)
expect("captured payer_id was redacted",
       captured.payer_id == "redacted-by-dsar")
expect("captured.redacted_at set",             captured.redacted_at is not None)

# CFDI retained with redacted rfc_receptor.
cfdis_after = asyncio.run(s.list_cfdis(tenant_id="tnt_x"))
expect("CFDI still listed (legal hold)",       len(cfdis_after) == 1)
expect("CFDI rfc_receptor redacted",
       cfdis_after[0].rfc_receptor == "REDACTED")
expect("CFDI.redacted_at set",                 cfdis_after[0].redacted_at is not None)


# ====================================================================
# 6. Cross-tenant isolation
# ====================================================================
section("6. Cross-tenant isolation")

s6 = InMemoryPaymentsStore(id_fn=_ids)
p_a = asyncio.run(s6.create_intent(tenant_id="tnt_a", payer_id="u_1",
                                   payee_id=None, amount_mxn=1.0))
asyncio.run(s6.capture_payment(payment_id=p_a.id))
asyncio.run(s6.create_intent(tenant_id="tnt_b", payer_id="u_1",
                             payee_id=None, amount_mxn=1.0))

asyncio.run(s6.purge_user_data(tenant_id="tnt_a", user_id="u_1"))
in_b = asyncio.run(s6.list_payments(tenant_id="tnt_b", user_id="u_1"))
expect("tnt_b is untouched",                   len(in_b) == 1)


# ====================================================================
# 7. main.py wiring guard
# ====================================================================
section("7. main.py wiring guard")

main_src = (SVC / "main.py").read_text(encoding="utf-8")
for needle in ("build_default_store", "set_payments_store",
               "_payments_store", "gather_user_data", "purge_user_data"):
    expect(f"payments/main.py uses {needle}",  needle in main_src, "")
expect("payments/main.py mount_dsar uses Protocol-driven path",
       "_payments_store.purge_user_data" in main_src, "")


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
print("  All payments store assertions green. ✅")
sys.exit(0)
