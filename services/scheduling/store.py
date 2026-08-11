"""
Scheduling — visits store (Phase 5.1).

Two concrete implementations of the `VisitsStore` Protocol:

  * `InMemoryVisitsStore` — test + dev default.
  * `PostgresVisitsStore`  — production. Backs the table in
    `services/scheduling/migrations/0001_init.sql`.

The Protocol is what `main.py` depends on. `build_default_store()`
picks the implementation based on `POSTGRES_URL`. This replaces the
ad-hoc `VisitStore` class from the MVP — the old Redis-backed path
still exists for caller compatibility but new code should use this
Protocol.

DSAR purge for visits is a real hard-delete (visits hold no fiscal
records). The DB-level workflow is soft-delete-then-nightly-cron
(`purge_deleted_visits()` from the migration); the in-memory variant
just pops the row directly.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------
@dataclass
class Visit:
    id: str
    tenant_id: str
    listing_id: str
    buyer_id: str
    agent_id: Optional[str] = None
    status: str = "proposed"
    proposed_slots: list = field(default_factory=list)
    selected_slot: Optional[dict] = None
    feedback: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    deleted_at: Optional[float] = None

    def public(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class VisitsStore(Protocol):
    async def create_visit(self, *, tenant_id: str, listing_id: str,
                           buyer_id: str,
                           proposed_slots: list,
                           agent_id: Optional[str] = None) -> Visit: ...
    async def confirm_visit(self, *, visit_id: str,
                            selected_slot: dict) -> Optional[Visit]: ...
    async def cancel_visit(self, *, visit_id: str) -> Optional[Visit]: ...
    async def add_feedback(self, *, visit_id: str,
                           feedback: dict) -> Optional[Visit]: ...
    async def get_visit(self, *, visit_id: str) -> Optional[Visit]: ...
    async def list_visits(self, *, tenant_id: str,
                          buyer_id: Optional[str] = None,
                          status: Optional[str] = None) -> list[Visit]: ...

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
class InMemoryVisitsStore:
    name = "in-memory"

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:10].upper(),
    ):
        self._now = now_fn
        self._id = id_fn
        self._visits: dict[str, Visit] = {}

    async def create_visit(self, *, tenant_id, listing_id, buyer_id,
                           proposed_slots, agent_id=None) -> Visit:
        vid = f"V-{self._id()}"
        v = Visit(
            id=vid, tenant_id=tenant_id, listing_id=listing_id,
            buyer_id=buyer_id, agent_id=agent_id,
            proposed_slots=list(proposed_slots),
            created_at=self._now(), updated_at=self._now(),
        )
        self._visits[vid] = v
        return v

    async def confirm_visit(self, *, visit_id, selected_slot):
        v = self._visits.get(visit_id)
        if v is None or v.deleted_at is not None:
            return None
        v.selected_slot = dict(selected_slot)
        v.status = "confirmed"
        v.updated_at = self._now()
        return v

    async def cancel_visit(self, *, visit_id):
        v = self._visits.get(visit_id)
        if v is None or v.deleted_at is not None:
            return None
        v.status = "cancelled"
        v.updated_at = self._now()
        return v

    async def add_feedback(self, *, visit_id, feedback):
        v = self._visits.get(visit_id)
        if v is None or v.deleted_at is not None:
            return None
        v.feedback = dict(feedback)
        v.status = "completed"
        v.updated_at = self._now()
        return v

    async def get_visit(self, *, visit_id):
        v = self._visits.get(visit_id)
        return v if (v and v.deleted_at is None) else None

    async def list_visits(self, *, tenant_id, buyer_id=None, status=None):
        out: list[Visit] = []
        for v in self._visits.values():
            if v.deleted_at is not None:
                continue
            if v.tenant_id != tenant_id:
                continue
            if buyer_id is not None and v.buyer_id != buyer_id:
                continue
            if status is not None and v.status != status:
                continue
            out.append(v)
        out.sort(key=lambda v: v.created_at, reverse=True)
        return out

    # ----- DSAR -----
    async def gather_user_data(self, *, tenant_id, user_id):
        mine = await self.list_visits(tenant_id=tenant_id, buyer_id=user_id)
        return {
            "visits": [v.public() for v in mine],
            "summary": {"visit_count": len(mine)},
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        # Visits hold no fiscal records — real hard-delete.
        dead = [
            vid for vid, v in self._visits.items()
            if v.tenant_id == tenant_id and v.buyer_id == user_id
        ]
        for vid in dead:
            del self._visits[vid]
        return {
            "count": len(dead), "deleted": True,
            "detail": {"visit_ids": dead[:50]},
        }

    # ----- lifecycle -----
    async def health(self):
        return {"backend": self.name,
                "visit_count": sum(1 for v in self._visits.values()
                                   if v.deleted_at is None)}

    async def close(self): ...


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------
class PostgresVisitsStore:
    name = "postgres"

    def __init__(
        self,
        *,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:10].upper(),
    ):
        self._id = id_fn

    async def _conn(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
        from db import with_conn                                 # type: ignore
        return with_conn()

    async def create_visit(self, *, tenant_id, listing_id, buyer_id,
                           proposed_slots, agent_id=None):
        import json as _json
        vid = f"V-{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO visits
                    (id, tenant_id, listing_id, buyer_id, agent_id,
                     proposed_slots)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id, tenant_id, listing_id, buyer_id, agent_id,
                          status, proposed_slots, selected_slot, feedback,
                          extract(epoch from created_at) AS created_at,
                          extract(epoch from updated_at) AS updated_at,
                          extract(epoch from deleted_at) AS deleted_at
                """,
                vid, tenant_id, listing_id, buyer_id, agent_id,
                _json.dumps(list(proposed_slots)),
            )
        return _row_to_visit(row)

    async def confirm_visit(self, *, visit_id, selected_slot):
        import json as _json
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE visits SET selected_slot = $2::jsonb,
                                  status = 'confirmed',
                                  updated_at = now(),
                                  starts_at = ($2::jsonb->>'start_iso')::timestamptz
                 WHERE id = $1 AND deleted_at IS NULL
                 RETURNING id, tenant_id, listing_id, buyer_id, agent_id,
                           status, proposed_slots, selected_slot, feedback,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from updated_at) AS updated_at,
                           extract(epoch from deleted_at) AS deleted_at
                """,
                visit_id, _json.dumps(dict(selected_slot)),
            )
        return _row_to_visit(row) if row else None

    async def cancel_visit(self, *, visit_id):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE visits SET status = 'cancelled', updated_at = now()
                 WHERE id = $1 AND deleted_at IS NULL
                 RETURNING id, tenant_id, listing_id, buyer_id, agent_id,
                           status, proposed_slots, selected_slot, feedback,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from updated_at) AS updated_at,
                           extract(epoch from deleted_at) AS deleted_at
                """,
                visit_id,
            )
        return _row_to_visit(row) if row else None

    async def add_feedback(self, *, visit_id, feedback):
        import json as _json
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                UPDATE visits SET feedback = $2::jsonb,
                                  status = 'completed',
                                  updated_at = now()
                 WHERE id = $1 AND deleted_at IS NULL
                 RETURNING id, tenant_id, listing_id, buyer_id, agent_id,
                           status, proposed_slots, selected_slot, feedback,
                           extract(epoch from created_at) AS created_at,
                           extract(epoch from updated_at) AS updated_at,
                           extract(epoch from deleted_at) AS deleted_at
                """,
                visit_id, _json.dumps(dict(feedback)),
            )
        return _row_to_visit(row) if row else None

    async def get_visit(self, *, visit_id):
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                SELECT id, tenant_id, listing_id, buyer_id, agent_id, status,
                       proposed_slots, selected_slot, feedback,
                       extract(epoch from created_at) AS created_at,
                       extract(epoch from updated_at) AS updated_at,
                       extract(epoch from deleted_at) AS deleted_at
                  FROM visits
                 WHERE id = $1 AND deleted_at IS NULL
                """,
                visit_id,
            )
        return _row_to_visit(row) if row else None

    async def list_visits(self, *, tenant_id, buyer_id=None, status=None):
        where = ["tenant_id = $1", "deleted_at IS NULL"]
        params: list[Any] = [tenant_id]
        if buyer_id is not None:
            params.append(buyer_id)
            where.append(f"buyer_id = ${len(params)}")
        if status is not None:
            params.append(status)
            where.append(f"status = ${len(params)}")
        sql = (
            "SELECT id, tenant_id, listing_id, buyer_id, agent_id, status, "
            "proposed_slots, selected_slot, feedback, "
            "extract(epoch from created_at) AS created_at, "
            "extract(epoch from updated_at) AS updated_at, "
            "extract(epoch from deleted_at) AS deleted_at "
            "FROM visits WHERE " + " AND ".join(where) +
            " ORDER BY created_at DESC"
        )
        async with await self._conn() as c:
            rows = await c.fetch(sql, *params)
        return [_row_to_visit(r) for r in rows]

    async def gather_user_data(self, *, tenant_id, user_id):
        mine = await self.list_visits(tenant_id=tenant_id, buyer_id=user_id)
        return {
            "visits": [v.public() for v in mine],
            "summary": {"visit_count": len(mine)},
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        # Soft-delete; the `purge_deleted_visits()` migration function
        # hard-removes after 30 days. That matches what the in-memory
        # variant exposes to the caller (count + deleted=True).
        async with await self._conn() as c:
            res = await c.execute(
                """
                UPDATE visits SET deleted_at = now()
                 WHERE tenant_id=$1 AND buyer_id=$2
                   AND deleted_at IS NULL
                """,
                tenant_id, user_id,
            )
        try:
            n = int(res.split(" ")[-1])
        except Exception:
            n = 0
        return {
            "count": n, "deleted": True,
            "detail": {"hard_delete_after_days": 30,
                       "soft_delete_now": True},
        }

    async def health(self):
        try:
            async with await self._conn() as c:
                n = await c.fetchval(
                    "SELECT count(*) FROM visits WHERE deleted_at IS NULL"
                )
            return {"backend": self.name, "visit_count": int(n or 0)}
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

    async def close(self): ...


def _row_to_visit(row) -> Visit:
    import json as _json
    proposed = row["proposed_slots"]
    if isinstance(proposed, str):
        try: proposed = _json.loads(proposed)
        except Exception: proposed = []
    selected = row["selected_slot"]
    if isinstance(selected, str):
        try: selected = _json.loads(selected)
        except Exception: selected = None
    feedback = row["feedback"]
    if isinstance(feedback, str):
        try: feedback = _json.loads(feedback)
        except Exception: feedback = None
    return Visit(
        id=row["id"], tenant_id=row["tenant_id"],
        listing_id=row["listing_id"], buyer_id=row["buyer_id"],
        agent_id=row["agent_id"],
        status=row["status"],
        proposed_slots=list(proposed or []),
        selected_slot=selected,
        feedback=feedback,
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        deleted_at=(float(row["deleted_at"])
                    if row["deleted_at"] is not None else None),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_default_store() -> VisitsStore:
    if os.getenv("POSTGRES_URL"):
        return PostgresVisitsStore()
    return InMemoryVisitsStore()
