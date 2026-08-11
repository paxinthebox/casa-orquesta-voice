"""
Payments + CFDI — store (Phase 5.1).

Two record kinds: `Payment` (intent → captured → refunded lifecycle)
and `Cfdi` (SAT-emitted XML wrappers). The store wraps both behind a
single Protocol; callers don't switch between two stores.

SAT 5-year retention is encoded the same way as documents:
  - SQL trigger `payments_set_retention()` in
    `migrations/0001_init.sql` sets `retention_until = now() + 5 years`
    on status → captured/refunded.
  - CFDIs default `retention_until = now() + 5 years` at INSERT.
  - `Payment.is_under_legal_hold()` and `Cfdi.is_under_legal_hold()`
    return the same answer for the in-memory variant. The DSAR purge
    consults them and redacts-instead-of-deleting anything still
    inside the window.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol


RETENTION_SECONDS = 5 * 365 * 24 * 3600


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class Payment:
    id: str
    tenant_id: str
    payer_id: Optional[str]
    payee_id: Optional[str]
    amount_mxn: float
    description: str = ""
    method: str = "card"
    status: str = "intent"
    provider: Optional[str] = None
    provider_intent_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    captured_at: Optional[float] = None
    retention_until: Optional[float] = None
    redacted_at: Optional[float] = None

    def public(self) -> dict:
        return asdict(self)

    def is_under_legal_hold(self, *, now: Optional[float] = None) -> bool:
        if self.retention_until is None:
            return False
        return (now if now is not None else time.time()) < self.retention_until


@dataclass
class Cfdi:
    id: str
    tenant_id: str
    payment_id: Optional[str]
    uuid_fiscal: str
    rfc_emisor: str
    rfc_receptor: str
    total_mxn: float
    xml_blob: str
    pdf_s3_key: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    emitted_at: float = field(default_factory=time.time)
    retention_until: float = field(
        default_factory=lambda: time.time() + RETENTION_SECONDS
    )
    redacted_at: Optional[float] = None

    def public(self) -> dict:
        return asdict(self)

    def is_under_legal_hold(self, *, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) < self.retention_until


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class PaymentsStore(Protocol):
    # ----- Payments -----
    async def create_intent(self, *, tenant_id: str,
                            payer_id: Optional[str], payee_id: Optional[str],
                            amount_mxn: float,
                            description: str = "",
                            method: str = "card",
                            provider: Optional[str] = None) -> Payment: ...
    async def capture_payment(self, *, payment_id: str,
                              provider_intent_id: Optional[str] = None
                              ) -> Optional[Payment]: ...
    async def refund_payment(self, *, payment_id: str) -> Optional[Payment]: ...
    async def get_payment(self, *, payment_id: str) -> Optional[Payment]: ...
    async def list_payments(self, *, tenant_id: str,
                            user_id: Optional[str] = None) -> list[Payment]: ...

    # ----- CFDI -----
    async def emit_cfdi(self, *, tenant_id: str,
                        payment_id: Optional[str],
                        uuid_fiscal: str,
                        rfc_emisor: str,
                        rfc_receptor: str,
                        total_mxn: float,
                        xml_blob: str,
                        pdf_s3_key: Optional[str] = None) -> Cfdi: ...
    async def list_cfdis(self, *, tenant_id: str,
                         rfc_receptor: Optional[str] = None) -> list[Cfdi]: ...

    # ----- DSAR -----
    async def gather_user_data(self, *, tenant_id: str,
                               user_id: str) -> dict: ...
    async def purge_user_data(self, *, tenant_id: str,
                              user_id: str) -> dict: ...

    # ----- lifecycle -----
    async def health(self) -> dict: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------
class InMemoryPaymentsStore:
    name = "in-memory"

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:10],
    ):
        self._now = now_fn
        self._id = id_fn
        self._payments: dict[str, Payment] = {}
        self._cfdis: dict[str, Cfdi] = {}

    # ----- Payments -----
    async def create_intent(self, *, tenant_id, payer_id, payee_id,
                            amount_mxn, description="", method="card",
                            provider=None):
        pid = f"pay_{self._id()}"
        p = Payment(
            id=pid, tenant_id=tenant_id,
            payer_id=payer_id, payee_id=payee_id,
            amount_mxn=float(amount_mxn),
            description=description, method=method,
            provider=provider, status="intent",
            created_at=self._now(),
        )
        self._payments[pid] = p
        return p

    async def capture_payment(self, *, payment_id, provider_intent_id=None):
        p = self._payments.get(payment_id)
        if p is None:
            return None
        p.status = "captured"
        p.captured_at = self._now()
        p.retention_until = self._now() + RETENTION_SECONDS
        if provider_intent_id is not None:
            p.provider_intent_id = provider_intent_id
        return p

    async def refund_payment(self, *, payment_id):
        p = self._payments.get(payment_id)
        if p is None:
            return None
        p.status = "refunded"
        if p.retention_until is None:
            p.retention_until = self._now() + RETENTION_SECONDS
        return p

    async def get_payment(self, *, payment_id):
        return self._payments.get(payment_id)

    async def list_payments(self, *, tenant_id, user_id=None):
        out: list[Payment] = []
        for p in self._payments.values():
            if p.tenant_id != tenant_id:
                continue
            if user_id is not None and not (
                p.payer_id == user_id or p.payee_id == user_id
            ):
                continue
            out.append(p)
        out.sort(key=lambda p: p.created_at, reverse=True)
        return out

    # ----- CFDI -----
    async def emit_cfdi(self, *, tenant_id, payment_id,
                        uuid_fiscal, rfc_emisor, rfc_receptor,
                        total_mxn, xml_blob, pdf_s3_key=None):
        cid = f"cfdi_{self._id()}"
        c = Cfdi(
            id=cid, tenant_id=tenant_id, payment_id=payment_id,
            uuid_fiscal=uuid_fiscal,
            rfc_emisor=rfc_emisor, rfc_receptor=rfc_receptor,
            total_mxn=float(total_mxn), xml_blob=xml_blob,
            pdf_s3_key=pdf_s3_key,
            emitted_at=self._now(),
            retention_until=self._now() + RETENTION_SECONDS,
        )
        self._cfdis[cid] = c
        return c

    async def list_cfdis(self, *, tenant_id, rfc_receptor=None):
        out: list[Cfdi] = []
        for c in self._cfdis.values():
            if c.tenant_id != tenant_id:
                continue
            if rfc_receptor is not None and c.rfc_receptor != rfc_receptor:
                continue
            out.append(c)
        out.sort(key=lambda c: c.emitted_at, reverse=True)
        return out

    # ----- DSAR -----
    async def gather_user_data(self, *, tenant_id, user_id):
        mine_payments = await self.list_payments(
            tenant_id=tenant_id, user_id=user_id,
        )
        mine_cfdis = [
            c for c in self._cfdis.values()
            if c.tenant_id == tenant_id
            and (self._payments.get(c.payment_id or "") and
                 (self._payments[c.payment_id or ""].payer_id == user_id
                  or self._payments[c.payment_id or ""].payee_id == user_id))
        ]
        return {
            "payments": [p.public() for p in mine_payments],
            "cfdis":    [c.public() for c in mine_cfdis],
            "summary": {
                "payment_count": len(mine_payments),
                "cfdi_count": len(mine_cfdis),
                "retention_note": "CFDIs retained 5y per SAT requirements",
            },
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        intents_dropped = 0
        payments_redacted = 0
        cfdis_redacted = 0
        # Drop unfunded intents; redact funded ones still under hold.
        for pid, p in list(self._payments.items()):
            if p.tenant_id != tenant_id:
                continue
            if not (p.payer_id == user_id or p.payee_id == user_id):
                continue
            if not p.is_under_legal_hold(now=self._now()) and \
               (p.status or "").lower() in ("intent", "failed", "cancelled"):
                del self._payments[pid]
                intents_dropped += 1
            else:
                if p.payer_id == user_id:
                    p.payer_id = "redacted-by-dsar"
                if p.payee_id == user_id:
                    p.payee_id = "redacted-by-dsar"
                p.redacted_at = self._now()
                payments_redacted += 1
        # CFDIs are ALWAYS retained for the 5-year SAT window — never drop.
        for cid, c in list(self._cfdis.items()):
            if c.tenant_id != tenant_id:
                continue
            # Match by linked payment (the relationship the in-memory
            # store has between (cfdi, payment, user) goes through the
            # cfdi.payment_id pointer).
            linked = self._payments.get(c.payment_id or "")
            if linked is None:
                continue
            # If the linked payment was redacted above, redact the cfdi too.
            if (linked.redacted_at is not None
                    or linked.payer_id == "redacted-by-dsar"
                    or linked.payee_id == "redacted-by-dsar"):
                c.rfc_receptor = "REDACTED"
                c.redacted_at = self._now()
                cfdis_redacted += 1
        return {
            "count": intents_dropped + payments_redacted + cfdis_redacted,
            "deleted": True,
            "detail": {
                "intents_dropped": intents_dropped,
                "payments_redacted": payments_redacted,
                "cfdis_redacted": cfdis_redacted,
                "retention_note": "SAT/CFDI 5y legal hold honored",
            },
        }

    async def health(self):
        return {
            "backend": self.name,
            "payment_count": len(self._payments),
            "cfdi_count": len(self._cfdis),
        }

    async def close(self): ...


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------
class PostgresPaymentsStore:
    name = "postgres"

    def __init__(
        self,
        *,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:10],
    ):
        self._id = id_fn

    async def _conn(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
        from db import with_conn                                 # type: ignore
        return with_conn()

    async def create_intent(self, *, tenant_id, payer_id, payee_id,
                            amount_mxn, description="", method="card",
                            provider=None):
        pid = f"pay_{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO payments
                    (id, tenant_id, payer_id, payee_id, amount_mxn,
                     description, method, provider)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                RETURNING id, tenant_id, payer_id, payee_id, amount_mxn,
                          description, method, status, provider,
                          provider_intent_id, metadata,
                          extract(epoch from created_at) AS created_at,
                          extract(epoch from captured_at) AS captured_at,
                          extract(epoch from retention_until) AS retention_until,
                          extract(epoch from redacted_at) AS redacted_at
                """,
                pid, tenant_id, payer_id, payee_id, float(amount_mxn),
                description, method, provider,
            )
        return _row_to_payment(row)

    async def capture_payment(self, *, payment_id, provider_intent_id=None):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE payments
                   SET status='captured',
                       captured_at = now(),
                       provider_intent_id = COALESCE($2, provider_intent_id)
                 WHERE id=$1
                 RETURNING id, tenant_id, payer_id, payee_id, amount_mxn,
                           description, method, status, provider,
                           provider_intent_id, metadata,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from captured_at) AS captured_at,
                           extract(epoch from retention_until) AS retention_until,
                           extract(epoch from redacted_at) AS redacted_at
                """,
                payment_id, provider_intent_id,
            )
        return _row_to_payment(row) if row else None

    async def refund_payment(self, *, payment_id):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE payments SET status='refunded' WHERE id=$1
                 RETURNING id, tenant_id, payer_id, payee_id, amount_mxn,
                           description, method, status, provider,
                           provider_intent_id, metadata,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from captured_at) AS captured_at,
                           extract(epoch from retention_until) AS retention_until,
                           extract(epoch from redacted_at) AS redacted_at
                """,
                payment_id,
            )
        return _row_to_payment(row) if row else None

    async def get_payment(self, *, payment_id):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                SELECT id, tenant_id, payer_id, payee_id, amount_mxn,
                       description, method, status, provider,
                       provider_intent_id, metadata,
                       extract(epoch from created_at) AS created_at,
                       extract(epoch from captured_at) AS captured_at,
                       extract(epoch from retention_until) AS retention_until,
                       extract(epoch from redacted_at) AS redacted_at
                  FROM payments WHERE id=$1
                """,
                payment_id,
            )
        return _row_to_payment(row) if row else None

    async def list_payments(self, *, tenant_id, user_id=None):
        where = ["tenant_id = $1"]
        params: list[Any] = [tenant_id]
        if user_id is not None:
            params.append(user_id)
            where.append(f"(payer_id = ${len(params)} OR payee_id = ${len(params)})")
        sql = (
            "SELECT id, tenant_id, payer_id, payee_id, amount_mxn, "
            "description, method, status, provider, provider_intent_id, "
            "metadata, "
            "extract(epoch from created_at) AS created_at, "
            "extract(epoch from captured_at) AS captured_at, "
            "extract(epoch from retention_until) AS retention_until, "
            "extract(epoch from redacted_at) AS redacted_at "
            "FROM payments WHERE " + " AND ".join(where) +
            " ORDER BY created_at DESC"
        )
        async with await self._conn() as c:
            rows = await c.fetch(sql, *params)
        return [_row_to_payment(r) for r in rows]

    async def emit_cfdi(self, *, tenant_id, payment_id, uuid_fiscal,
                        rfc_emisor, rfc_receptor, total_mxn, xml_blob,
                        pdf_s3_key=None):
        cid = f"cfdi_{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO cfdis
                    (id, tenant_id, payment_id, uuid_fiscal,
                     rfc_emisor, rfc_receptor, total_mxn, xml_blob,
                     pdf_s3_key)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                RETURNING id, tenant_id, payment_id, uuid_fiscal,
                          rfc_emisor, rfc_receptor, total_mxn, xml_blob,
                          pdf_s3_key, metadata,
                          extract(epoch from emitted_at) AS emitted_at,
                          extract(epoch from retention_until) AS retention_until,
                          extract(epoch from redacted_at) AS redacted_at
                """,
                cid, tenant_id, payment_id, uuid_fiscal,
                rfc_emisor, rfc_receptor, float(total_mxn), xml_blob,
                pdf_s3_key,
            )
        return _row_to_cfdi(row)

    async def list_cfdis(self, *, tenant_id, rfc_receptor=None):
        where = ["tenant_id = $1"]
        params: list[Any] = [tenant_id]
        if rfc_receptor is not None:
            params.append(rfc_receptor)
            where.append(f"rfc_receptor = ${len(params)}")
        sql = (
            "SELECT id, tenant_id, payment_id, uuid_fiscal, rfc_emisor, "
            "rfc_receptor, total_mxn, xml_blob, pdf_s3_key, metadata, "
            "extract(epoch from emitted_at) AS emitted_at, "
            "extract(epoch from retention_until) AS retention_until, "
            "extract(epoch from redacted_at) AS redacted_at "
            "FROM cfdis WHERE " + " AND ".join(where) +
            " ORDER BY emitted_at DESC"
        )
        async with await self._conn() as c:
            rows = await c.fetch(sql, *params)
        return [_row_to_cfdi(r) for r in rows]

    async def gather_user_data(self, *, tenant_id, user_id):
        mine_payments = await self.list_payments(
            tenant_id=tenant_id, user_id=user_id,
        )
        # CFDIs joined through payment_id linkage.
        payment_ids = [p.id for p in mine_payments]
        if payment_ids:
            async with await self._conn() as c:
                rows = await c.fetch(
                    """
                    SELECT id, tenant_id, payment_id, uuid_fiscal,
                           rfc_emisor, rfc_receptor, total_mxn, xml_blob,
                           pdf_s3_key, metadata,
                           extract(epoch from emitted_at) AS emitted_at,
                           extract(epoch from retention_until) AS retention_until,
                           extract(epoch from redacted_at) AS redacted_at
                      FROM cfdis
                     WHERE tenant_id=$1 AND payment_id = ANY($2::text[])
                    """,
                    tenant_id, payment_ids,
                )
            mine_cfdis = [_row_to_cfdi(r) for r in rows]
        else:
            mine_cfdis = []
        return {
            "payments": [p.public() for p in mine_payments],
            "cfdis":    [c.public() for c in mine_cfdis],
            "summary": {
                "payment_count": len(mine_payments),
                "cfdi_count": len(mine_cfdis),
                "retention_note": "CFDIs retained 5y per SAT requirements",
            },
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        async with await self._conn() as c:
            drop_res = await c.execute(
                """
                DELETE FROM payments
                 WHERE tenant_id=$1
                   AND (payer_id=$2 OR payee_id=$2)
                   AND status IN ('intent','failed','cancelled')
                   AND (retention_until IS NULL OR retention_until <= now())
                """,
                tenant_id, user_id,
            )
            payments_res = await c.execute(
                """
                UPDATE payments
                   SET payer_id = CASE WHEN payer_id=$2 THEN 'redacted-by-dsar' ELSE payer_id END,
                       payee_id = CASE WHEN payee_id=$2 THEN 'redacted-by-dsar' ELSE payee_id END,
                       redacted_at = now()
                 WHERE tenant_id=$1
                   AND (payer_id=$2 OR payee_id=$2)
                   AND status NOT IN ('intent','failed','cancelled')
                """,
                tenant_id, user_id,
            )
            # CFDIs follow the redacted payment.
            cfdi_res = await c.execute(
                """
                UPDATE cfdis
                   SET rfc_receptor='REDACTED', redacted_at=now()
                 WHERE tenant_id=$1
                   AND payment_id IN (
                       SELECT id FROM payments
                        WHERE tenant_id=$1 AND payer_id='redacted-by-dsar'
                   )
                """,
                tenant_id,
            )
        intents_dropped = int(drop_res.split(" ")[-1]) if drop_res else 0
        payments_redacted = int(payments_res.split(" ")[-1]) if payments_res else 0
        cfdis_redacted = int(cfdi_res.split(" ")[-1]) if cfdi_res else 0
        return {
            "count": intents_dropped + payments_redacted + cfdis_redacted,
            "deleted": True,
            "detail": {
                "intents_dropped": intents_dropped,
                "payments_redacted": payments_redacted,
                "cfdis_redacted": cfdis_redacted,
                "retention_note": "SAT/CFDI 5y legal hold honored",
            },
        }

    async def health(self):
        try:
            async with await self._conn() as c:
                p = await c.fetchval("SELECT count(*) FROM payments")
                cf = await c.fetchval("SELECT count(*) FROM cfdis")
            return {"backend": self.name,
                    "payment_count": int(p or 0),
                    "cfdi_count": int(cf or 0)}
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

    async def close(self): ...


def _row_to_payment(row) -> Payment:
    import json as _json
    metadata = row["metadata"]
    if isinstance(metadata, str):
        try: metadata = _json.loads(metadata)
        except Exception: metadata = {}
    return Payment(
        id=row["id"], tenant_id=row["tenant_id"],
        payer_id=row["payer_id"], payee_id=row["payee_id"],
        amount_mxn=float(row["amount_mxn"]),
        description=row["description"] or "",
        method=row["method"], status=row["status"],
        provider=row["provider"],
        provider_intent_id=row["provider_intent_id"],
        metadata=dict(metadata or {}),
        created_at=float(row["created_at"]),
        captured_at=(float(row["captured_at"])
                     if row["captured_at"] is not None else None),
        retention_until=(float(row["retention_until"])
                         if row["retention_until"] is not None else None),
        redacted_at=(float(row["redacted_at"])
                     if row["redacted_at"] is not None else None),
    )


def _row_to_cfdi(row) -> Cfdi:
    import json as _json
    metadata = row["metadata"]
    if isinstance(metadata, str):
        try: metadata = _json.loads(metadata)
        except Exception: metadata = {}
    return Cfdi(
        id=row["id"], tenant_id=row["tenant_id"],
        payment_id=row["payment_id"],
        uuid_fiscal=row["uuid_fiscal"],
        rfc_emisor=row["rfc_emisor"], rfc_receptor=row["rfc_receptor"],
        total_mxn=float(row["total_mxn"]),
        xml_blob=row["xml_blob"], pdf_s3_key=row["pdf_s3_key"],
        metadata=dict(metadata or {}),
        emitted_at=float(row["emitted_at"]),
        retention_until=float(row["retention_until"]),
        redacted_at=(float(row["redacted_at"])
                     if row["redacted_at"] is not None else None),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_default_store() -> PaymentsStore:
    if os.getenv("POSTGRES_URL"):
        return PostgresPaymentsStore()
    return InMemoryPaymentsStore()
