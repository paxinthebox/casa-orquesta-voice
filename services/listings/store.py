"""
Listings — user-indexed store (Phase 4.9).

Two concrete implementations of the same `ListingsUserStore` Protocol:

  * `InMemoryListingsUserStore` — test + dev default. Pure dict state.
  * `PostgresListingsUserStore`  — production. Uses
    `services/_shared/db.py` connection pool. Reads + writes against
    the tables defined in `migrations/0001_init.sql`.

The Protocol is what callers depend on. `main.py` picks the concrete
implementation based on `POSTGRES_URL`. Tests inject the in-memory
variant directly so the standalone runner stays hermetic.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass
class Favorite:
    id: str
    tenant_id: str
    user_id: str
    listing_id: str
    note: str = ""
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


@dataclass
class SavedSearch:
    id: str
    tenant_id: str
    user_id: str
    label: str
    query_text: str
    filters: dict = field(default_factory=dict)
    last_run_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------
class ListingsUserStore(Protocol):
    # ----- favorites -----
    async def add_favorite(self, *, tenant_id: str, user_id: str,
                           listing_id: str, note: str = "") -> Favorite: ...
    async def remove_favorite(self, *, tenant_id: str, user_id: str,
                              listing_id: str) -> int: ...
    async def list_favorites(self, *, tenant_id: str,
                             user_id: str) -> list[Favorite]: ...

    # ----- saved searches -----
    async def add_search(self, *, tenant_id: str, user_id: str,
                         label: str, query_text: str,
                         filters: dict) -> SavedSearch: ...
    async def list_searches(self, *, tenant_id: str,
                            user_id: str) -> list[SavedSearch]: ...
    async def touch_search(self, *, search_id: str) -> None: ...

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
class InMemoryListingsUserStore:
    name = "in-memory"

    def __init__(
        self,
        *,
        now_fn: Callable[[], float] = time.time,
        id_fn: Callable[[], str] = lambda: uuid.uuid4().hex[:12],
    ):
        self._now = now_fn
        self._id = id_fn
        self._favorites: dict[str, Favorite] = {}
        self._searches: dict[str, SavedSearch] = {}

    # ----- favorites -----
    async def add_favorite(self, *, tenant_id: str, user_id: str,
                           listing_id: str, note: str = "") -> Favorite:
        # Enforce the (tenant, user, listing) uniqueness constraint.
        for f in self._favorites.values():
            if (f.tenant_id == tenant_id and f.user_id == user_id
                    and f.listing_id == listing_id):
                # Update the note in-place; return the existing row.
                f.note = note or f.note
                return f
        fav = Favorite(
            id=f"fav_{self._id()}",
            tenant_id=tenant_id, user_id=user_id,
            listing_id=listing_id, note=note,
            created_at=self._now(),
        )
        self._favorites[fav.id] = fav
        return fav

    async def remove_favorite(self, *, tenant_id: str, user_id: str,
                              listing_id: str) -> int:
        doomed = [
            fid for fid, f in self._favorites.items()
            if f.tenant_id == tenant_id
            and f.user_id == user_id
            and f.listing_id == listing_id
        ]
        for fid in doomed:
            del self._favorites[fid]
        return len(doomed)

    async def list_favorites(self, *, tenant_id: str,
                             user_id: str) -> list[Favorite]:
        return sorted(
            (f for f in self._favorites.values()
             if f.tenant_id == tenant_id and f.user_id == user_id),
            key=lambda f: f.created_at,
            reverse=True,
        )

    # ----- searches -----
    async def add_search(self, *, tenant_id: str, user_id: str,
                         label: str, query_text: str,
                         filters: dict) -> SavedSearch:
        s = SavedSearch(
            id=f"srch_{self._id()}",
            tenant_id=tenant_id, user_id=user_id,
            label=label, query_text=query_text,
            filters=dict(filters),
            created_at=self._now(),
        )
        self._searches[s.id] = s
        return s

    async def list_searches(self, *, tenant_id: str,
                            user_id: str) -> list[SavedSearch]:
        return sorted(
            (s for s in self._searches.values()
             if s.tenant_id == tenant_id and s.user_id == user_id),
            key=lambda s: s.created_at,
            reverse=True,
        )

    async def touch_search(self, *, search_id: str) -> None:
        s = self._searches.get(search_id)
        if s is not None:
            s.last_run_at = self._now()

    # ----- DSAR -----
    async def gather_user_data(self, *, tenant_id: str,
                               user_id: str) -> dict:
        favs = [f.public() for f in await self.list_favorites(
            tenant_id=tenant_id, user_id=user_id)]
        searches = [s.public() for s in await self.list_searches(
            tenant_id=tenant_id, user_id=user_id)]
        return {
            "favorites": favs,
            "saved_searches": searches,
            "summary": {
                "favorite_count": len(favs),
                "saved_search_count": len(searches),
            },
        }

    async def purge_user_data(self, *, tenant_id: str,
                              user_id: str) -> dict:
        fav_dead = [
            fid for fid, f in self._favorites.items()
            if f.tenant_id == tenant_id and f.user_id == user_id
        ]
        srch_dead = [
            sid for sid, s in self._searches.items()
            if s.tenant_id == tenant_id and s.user_id == user_id
        ]
        for fid in fav_dead:
            del self._favorites[fid]
        for sid in srch_dead:
            del self._searches[sid]
        return {
            "count": len(fav_dead) + len(srch_dead),
            "deleted": True,
            "detail": {
                "favorites_removed": len(fav_dead),
                "saved_searches_removed": len(srch_dead),
            },
        }

    # ----- lifecycle -----
    async def health(self) -> dict:
        return {
            "backend": self.name,
            "favorite_count": len(self._favorites),
            "saved_search_count": len(self._searches),
        }

    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------
class PostgresListingsUserStore:
    name = "postgres"

    def __init__(self, *, id_fn: Callable[[], str]
                 = lambda: uuid.uuid4().hex[:12]):
        self._id = id_fn

    async def _conn(self):
        # Import here so test envs without _shared on PYTHONPATH (or
        # without asyncpg installed) can still load this module.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))
        from db import with_conn                                 # type: ignore
        return with_conn()

    async def add_favorite(self, *, tenant_id, user_id, listing_id,
                           note=""):
        fid = f"fav_{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO favorites (id, tenant_id, user_id, listing_id, note)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tenant_id, user_id, listing_id)
                DO UPDATE SET note = EXCLUDED.note
                RETURNING id, tenant_id, user_id, listing_id, note,
                          extract(epoch from created_at) AS created_at
                """,
                fid, tenant_id, user_id, listing_id, note,
            )
        return Favorite(
            id=row["id"], tenant_id=row["tenant_id"],
            user_id=row["user_id"], listing_id=row["listing_id"],
            note=row["note"] or "",
            created_at=float(row["created_at"]),
        )

    async def remove_favorite(self, *, tenant_id, user_id, listing_id):
        async with await self._conn() as c:
            res = await c.execute(
                "DELETE FROM favorites WHERE tenant_id=$1 AND user_id=$2 "
                "AND listing_id=$3",
                tenant_id, user_id, listing_id,
            )
        # asyncpg returns "DELETE N" where N is the row count.
        try:
            return int(res.split(" ")[-1])
        except Exception:
            return 0

    async def list_favorites(self, *, tenant_id, user_id):
        async with await self._conn() as c:
            rows = await c.fetch(
                """
                SELECT id, tenant_id, user_id, listing_id, note,
                       extract(epoch from created_at) AS created_at
                  FROM favorites
                 WHERE tenant_id=$1 AND user_id=$2
                 ORDER BY created_at DESC
                """,
                tenant_id, user_id,
            )
        return [
            Favorite(id=r["id"], tenant_id=r["tenant_id"],
                     user_id=r["user_id"], listing_id=r["listing_id"],
                     note=r["note"] or "", created_at=float(r["created_at"]))
            for r in rows
        ]

    async def add_search(self, *, tenant_id, user_id, label,
                         query_text, filters):
        sid = f"srch_{self._id()}"
        async with await self._conn() as c:
            row = await c.fetchrow(
                """
                INSERT INTO saved_searches
                    (id, tenant_id, user_id, label, query_text, filters)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                RETURNING id, tenant_id, user_id, label, query_text,
                          filters,
                          extract(epoch from created_at) AS created_at,
                          extract(epoch from last_run_at) AS last_run_at
                """,
                sid, tenant_id, user_id, label, query_text,
                json.dumps(dict(filters)),
            )
        return _row_to_search(row)

    async def list_searches(self, *, tenant_id, user_id):
        async with await self._conn() as c:
            rows = await c.fetch(
                """
                SELECT id, tenant_id, user_id, label, query_text, filters,
                       extract(epoch from created_at) AS created_at,
                       extract(epoch from last_run_at) AS last_run_at
                  FROM saved_searches
                 WHERE tenant_id=$1 AND user_id=$2
                 ORDER BY created_at DESC
                """,
                tenant_id, user_id,
            )
        return [_row_to_search(r) for r in rows]

    async def touch_search(self, *, search_id):
        async with await self._conn() as c:
            await c.execute(
                "UPDATE saved_searches SET last_run_at = now() WHERE id=$1",
                search_id,
            )

    async def gather_user_data(self, *, tenant_id, user_id):
        favs = [f.public() for f in await self.list_favorites(
            tenant_id=tenant_id, user_id=user_id)]
        srchs = [s.public() for s in await self.list_searches(
            tenant_id=tenant_id, user_id=user_id)]
        return {
            "favorites": favs,
            "saved_searches": srchs,
            "summary": {
                "favorite_count": len(favs),
                "saved_search_count": len(srchs),
            },
        }

    async def purge_user_data(self, *, tenant_id, user_id):
        async with await self._conn() as c:
            fav_res = await c.execute(
                "DELETE FROM favorites WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )
            srch_res = await c.execute(
                "DELETE FROM saved_searches WHERE tenant_id=$1 AND user_id=$2",
                tenant_id, user_id,
            )
        fav_n = int(fav_res.split(" ")[-1]) if fav_res else 0
        srch_n = int(srch_res.split(" ")[-1]) if srch_res else 0
        return {
            "count": fav_n + srch_n,
            "deleted": True,
            "detail": {
                "favorites_removed": fav_n,
                "saved_searches_removed": srch_n,
            },
        }

    async def health(self):
        try:
            async with await self._conn() as c:
                fav_n = await c.fetchval("SELECT count(*) FROM favorites")
                srch_n = await c.fetchval("SELECT count(*) FROM saved_searches")
            return {
                "backend": self.name,
                "favorite_count": int(fav_n or 0),
                "saved_search_count": int(srch_n or 0),
            }
        except Exception as e:
            return {"backend": self.name, "error": str(e)}

    async def close(self): ...


def _row_to_search(row) -> SavedSearch:
    filters = row["filters"]
    if isinstance(filters, str):
        try: filters = json.loads(filters)
        except Exception: filters = {}
    return SavedSearch(
        id=row["id"], tenant_id=row["tenant_id"],
        user_id=row["user_id"], label=row["label"] or "",
        query_text=row["query_text"],
        filters=dict(filters or {}),
        last_run_at=(float(row["last_run_at"])
                     if row["last_run_at"] is not None else None),
        created_at=float(row["created_at"]),
    )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_default_store() -> ListingsUserStore:
    if os.getenv("POSTGRES_URL"):
        return PostgresListingsUserStore()
    return InMemoryListingsUserStore()
