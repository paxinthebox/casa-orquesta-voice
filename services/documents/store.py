"""
Documents — store (Phase 5.1).

`DocumentsStore` Protocol + `InMemoryDocumentsStore` + `PostgresDocumentsStore`.

CFDI 4.0 art. 30 retention contract is encoded in two places that MUST
agree:
  - SQL trigger `documents_set_retention()` in `migrations/0001_init.sql`
    auto-populates `retention_until = now() + 5 years` when a row
    transitions to `status = 'signed'`.
  - This module's `Document.is_under_legal_hold()` returns the same
    answer for the in-memory variant. The DSAR purge consults it.

The behavioral contract is identical to what the P4.6 in-line DSAR
logic in `services/documents/main.py` already enforces — this just
moves it behind a Protocol so production + tests agree.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol


# CFDI 4.0 art. 30 retention window (5 years from sign).
RETENTION_SECONDS = 5 * 365 * 24 * 3600


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
@dataclass
class Document:
    id: str
    tenant_id: str
    buyer_id: str
    owner_id: Optional[str] = None
    listing_id: Optional[str] = None
    kind: str = "promesa_compraventa"
    status: str = "draft"
    sha256: Optional[str] = None
    s3_key: Optional[str] = None
    signers: dict = field(default_factory=dict)
    nom151_token: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    signed_at: Optional[float] = None
    retention_until: Optional[float] = None
    redacted_at: Optional[float] = None

    def public(self) -> dict:
        return asdict(self)

    def is_under_legal_hold(self, *, now: Optional[float] = None) -> bool:
        if self.retention_until is None:
            return False
        return (now if now is not None else time.time()) < self.retention_until


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class DocumentsStore(Protocol):
    async def create_document(self, *, tenant_id: str, buyer_id: str,
                              kind: str = "promesa_compraventa",
                              owner_id: Optional[str] = None,
                              listing_id: Optional[str] = None,
                              s3_key: Optional[str] = None,
                              sha256: Optional[str] = None,
                              metadata: Optional[dict] = None) -> Document: ...
    async def attach_signer(self, *, doc_id: str, role: str,
                            signer_id: str,
                            signature_hash: str) -> Optional[Document]: ...
    async def mark_signed(self, *, doc_id: str,
                          nom151_token: Optional[str] = None
                          ) -> Optional[Document]: ...
    async def get_document(self, *, doc_id: str) -> Optional[Document]: ...
    async def list_documents(self, *, tenant_id: str,
                             buyer_id: Optional[str] = None,
                             status: Optional[str] = None) -> list[Document]: ...

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
class InMemoryDocumentsStore:
    name = "in-memory"

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:10],
    ):
        self._now = now_fn
        self._id = id_fn
        self._docs: dict[str, Document] = {}

    async def create_document(self, *, tenant_id, buyer_id,
                              kind="promesa_compraventa",
                              owner_id=None, listing_id=None,
                              s3_key=None, sha256=None,
                              metadata=None):
        did = f"doc_{self._id()}"
        d = Document(
            id=did, tenant_id=tenant_id, buyer_id=buyer_id,
            owner_id=owner_id, listing_id=listing_id, kind=kind,
            s3_key=s3_key, sha256=sha256,
            metadata=dict(metadata or {}),
            created_at=self._now(),
        )
        self._docs[did] = d
        return d

    async def attach_signer(self, *, doc_id, role, signer_id,
                            signature_hash):
        d = self._docs.get(doc_id)
        if d is None:
            return None
        d.signers[role] = {
            "signer_id": signer_id,
            "signature_hash": signature_hash,
            "ts": self._now(),
        }
        # Status transitions mirror the MVP behavior — both signers ⇒
        # 'signed' (and the legal-hold clock starts). One signer ⇒
        # 'partially_signed'.
        roles_signed = set(d.signers.keys())
        if {"buyer", "owner"}.issubset(roles_signed):
            await self._do_sign(d)
        elif d.signers:
            d.status = "partially_signed"
        return d

    async def mark_signed(self, *, doc_id, nom151_token=None):
        d = self._docs.get(doc_id)
        if d is None:
            return None
        d.nom151_token = nom151_token or d.nom151_token
        await self._do_sign(d)
        return d

    async def _do_sign(self, d: Document) -> None:
        if d.status == "signed":
            return
        d.status = "signed"
        d.signed_at = self._now()
        d.retention_until = self._now() + RETENTION_SECONDS

    async def get_document(self, *, doc_id):
        return self._docs.get(doc_id)

    async def list_documents(self, *, tenant_id, buyer_id=None,
                             status=None):
        out: list[Document] = []
        for d in self._docs.values():
            if d.tenant_id != tenant_id:
                continue
            if buyer_id is not None and d.buyer_id != buyer_id:
                continue
            if status is not None and d.status != status:
                continue
            out.append(d)
        out.sort(key=lambda d: d.created_at, reverse=True)
        return out

    # ----- DSAR -----
    async def gather_user_data(self, *, tenant_id, user_id):
        mine = await self.list_documents(tenant_id=tenant_id, buyer_id=user_id)
        return {
            "documents": [d.public() for d in mine],
            "summary": {
                "document_count": len(mine),
                "retention_note": "signed records retained 5y per CFDI 4.0",
            },
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        """Drop unsigned drafts; redact PII fields on signed docs that are
        still inside the 5-year window."""
        drafts_dropped = 0
        signed_redacted = 0
        for did, d in list(self._docs.items()):
            if d.tenant_id != tenant_id or d.buyer_id != user_id:
                continue
            if d.is_under_legal_hold(now=self._now()):
                d.buyer_id = "redacted-by-dsar"
                d.signers = {k: {"redacted": True} for k in d.signers}
                d.redacted_at = self._now()
                signed_redacted += 1
            else:
                del self._docs[did]
                drafts_dropped += 1
        return {
            "count": drafts_dropped + signed_redacted,
            "deleted": True,
            "detail": {
                "drafts_dropped": drafts_dropped,
                "signed_redacted": signed_redacted,
                "retention_note": "signed records redacted, not deleted (CFDI 5y)",
            },
        }

    # ----- lifecycle -----
    async def health(self):
        return {"backend": self.name, "document_count": len(self._docs)}

    async def close(self): ...


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------
class PostgresDocumentsStore:
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

    async def create_document(self, *, tenant_id, buyer_id,
                              kind="promesa_compraventa",
                              owner_id=None, listing_id=None,
                              s3_key=None, sha256=None,
                              metadata=None):
        import json as _json
        did = f"doc_{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO documents
                    (id, tenant_id, buyer_id, owner_id, listing_id,
                     kind, s3_key, sha256, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                RETURNING id, tenant_id, buyer_id, owner_id, listing_id,
                          kind, status, sha256, s3_key, signers, nom151_token,
                          metadata,
                          extract(epoch from created_at) AS created_at,
                          extract(epoch from signed_at) AS signed_at,
                          extract(epoch from retention_until) AS retention_until,
                          extract(epoch from redacted_at) AS redacted_at
                """,
                did, tenant_id, buyer_id, owner_id, listing_id,
                kind, s3_key, sha256, _json.dumps(dict(metadata or {})),
            )
        return _row_to_doc(row)

    async def attach_signer(self, *, doc_id, role, signer_id,
                            signature_hash):
        import json as _json
        async with await self._conn() as c:
            # Read the current signers, merge, and write back.
            existing = await c.fetchval(
                "SELECT signers FROM documents WHERE id = $1", doc_id,
            )
            if existing is None:
                return None
            if isinstance(existing, str):
                try: existing = _json.loads(existing)
                except Exception: existing = {}
            sigs = dict(existing or {})
            sigs[role] = {
                "signer_id": signer_id,
                "signature_hash": signature_hash,
                "ts": time.time(),
            }
            new_status = ("signed" if {"buyer", "owner"}.issubset(sigs)
                          else "partially_signed")
            row = await c.fetchrow(
                """
                UPDATE documents
                   SET signers = $2::jsonb,
                       status  = $3
                 WHERE id = $1
                 RETURNING id, tenant_id, buyer_id, owner_id, listing_id,
                           kind, status, sha256, s3_key, signers, nom151_token,
                           metadata,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from signed_at) AS signed_at,
                           extract(epoch from retention_until) AS retention_until,
                           extract(epoch from redacted_at) AS redacted_at
                """,
                doc_id, _json.dumps(sigs), new_status,
            )
        return _row_to_doc(row) if row else None

    async def mark_signed(self, *, doc_id, nom151_token=None):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE documents SET status='signed',
                                     nom151_token=COALESCE($2, nom151_token)
                 WHERE id = $1
                 RETURNING id, tenant_id, buyer_id, owner_id, listing_id,
                           kind, status, sha256, s3_key, signers, nom151_token,
                           metadata,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from signed_at) AS signed_at,
                           extract(epoch from retention_until) AS retention_until,
                           extract(epoch from redacted_at) AS redacted_at
                """,
                doc_id, nom151_token,
            )
        return _row_to_doc(row) if row else None

    async def get_document(self, *, doc_id):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                SELECT id, tenant_id, buyer_id, owner_id, listing_id, kind,
                       status, sha256, s3_key, signers, nom151_token, metadata,
                       extract(epoch from created_at) AS created_at,
                       extract(epoch from signed_at) AS signed_at,
                       extract(epoch from retention_until) AS retention_until,
                       extract(epoch from redacted_at) AS redacted_at
                  FROM documents WHERE id = $1
                """,
                doc_id,
            )
        return _row_to_doc(row) if row else None

    async def list_documents(self, *, tenant_id, buyer_id=None,
                             status=None):
        where = ["tenant_id = $1"]
        params: list[Any] = [tenant_id]
        if buyer_id is not None:
            params.append(buyer_id)
            where.append(f"buyer_id = ${len(params)}")
        if status is not None:
            params.append(status)
            where.append(f"status = ${len(params)}")
        sql = (
            "SELECT id, tenant_id, buyer_id, owner_id, listing_id, kind, "
            "status, sha256, s3_key, signers, nom151_token, metadata, "
            "extract(epoch from created_at) AS created_at, "
            "extract(epoch from signed_at) AS signed_at, "
            "extract(epoch from retention_until) AS retention_until, "
            "extract(epoch from redacted_at) AS redacted_at "
            "FROM documents WHERE " + " AND ".join(where) +
            " ORDER BY created_at DESC"
        )
        async with await self._conn() as c:
            rows = await c.fetch(sql, *params)
        return [_row_to_doc(r) for r in rows]

    async def gather_user_data(self, *, tenant_id, user_id):
        mine = await self.list_documents(tenant_id=tenant_id, buyer_id=user_id)
        return {
            "documents": [d.public() for d in mine],
            "summary": {
                "document_count": len(mine),
                "retention_note": "signed records retained 5y per CFDI 4.0",
            },
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        """Same contract as the in-memory variant: drop drafts,
        redact-don't-delete anything inside the legal hold."""
        async with await self._conn() as c:
            # Hard-delete drafts (no hold).
            drop_res = await c.execute(
                """
                DELETE FROM documents
                 WHERE tenant_id=$1 AND buyer_id=$2
                   AND (retention_until IS NULL
                        OR retention_until <= now())
                """,
                tenant_id, user_id,
            )
            # Redact-in-place for signed records still under hold.
            redact_res = await c.execute(
                """
                UPDATE documents
                   SET buyer_id='redacted-by-dsar',
                       signers='{}'::jsonb,
                       redacted_at = now()
                 WHERE tenant_id=$1 AND buyer_id=$2
                   AND retention_until IS NOT NULL
                   AND retention_until > now()
                """,
                tenant_id, user_id,
            )
        drafts_dropped = int(drop_res.split(" ")[-1]) if drop_res else 0
        signed_redacted = int(redact_res.split(" ")[-1]) if redact_res else 0
        return {
            "count": drafts_dropped + signed_redacted,
            "deleted": True,
            "detail": {
                "drafts_dropped": drafts_dropped,
                "signed_redacted": signed_redacted,
                "retention_note": "signed records redacted, not deleted (CFDI 5y)",
            },
        }

    async def health(self):
        try:
            async with await self._conn() as c:
                n = await c.fetchval("SELECT count(*) FROM documents")
            return {"backend": self.name, "document_count": int(n or 0)}
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

    async def close(self): ...


def _row_to_doc(row) -> Document:
    import json as _json
    signers = row["signers"]
    if isinstance(signers, str):
        try: signers = _json.loads(signers)
        except Exception: signers = {}
    metadata = row["metadata"]
    if isinstance(metadata, str):
        try: metadata = _json.loads(metadata)
        except Exception: metadata = {}
    return Document(
        id=row["id"], tenant_id=row["tenant_id"],
        buyer_id=row["buyer_id"], owner_id=row["owner_id"],
        listing_id=row["listing_id"], kind=row["kind"],
        status=row["status"], sha256=row["sha256"],
        s3_key=row["s3_key"], signers=dict(signers or {}),
        nom151_token=row["nom151_token"],
        metadata=dict(metadata or {}),
        created_at=float(row["created_at"]),
        signed_at=(float(row["signed_at"])
                   if row["signed_at"] is not None else None),
        retention_until=(float(row["retention_until"])
                         if row["retention_until"] is not None else None),
        redacted_at=(float(row["redacted_at"])
                     if row["redacted_at"] is not None else None),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_default_store() -> DocumentsStore:
    if os.getenv("POSTGRES_URL"):
        return PostgresDocumentsStore()
    return InMemoryDocumentsStore()
