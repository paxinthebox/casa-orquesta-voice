"""
Casa·Orquesta — Listings Service.

CRUD over RESO-aligned property records.

Phase 1.4 status: ported from MVP with two changes:
  1. Storage: SQLite → Postgres (pgvector enabled). Connection via POSTGRES_URL.
     A `vector(1024)` column is reserved on the listings table for BGE-M3
     embeddings; population happens in a later phase.
  2. Migrations: a tiny Flyway-style runner (we deliberately avoid the Flyway
     Java tool) applies the SQL files in migrations/ in lexical order, tracking
     applied versions in a `schema_migrations` table.

Behavior surface (the HTTP API) is byte-identical to MVP:
  - GET  /listings           filter by state/type/beds_min/price_max_mxn, limit
  - GET  /listings/{id}
  - POST /listings
  - GET  /stats
  - GET  /health             reports storage backend
"""
import os
import json
import glob
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

POSTGRES_URL = os.getenv("POSTGRES_URL", "")
SEED_PATH = os.getenv("SEED_PATH", "/app/seeds/listings.json")
MIGRATIONS_DIR = os.getenv("MIGRATIONS_DIR",
                           os.path.join(os.path.dirname(__file__), "migrations"))

def _has_live_rent_catalog() -> bool:
    return os.getenv("LISTINGS_HAS_LIVE_RENT", "").strip().lower() in {
        "1", "true", "yes",
    }


def _note_live_rent_row(data: dict) -> None:
    """Set process flag when portal rent rows are upserted (push or cache load)."""
    mode = str(data.get("listing_mode") or "sale").lower()
    if mode != "rent":
        return
    if _is_live_listing(data):
        os.environ["LISTINGS_HAS_LIVE_RENT"] = "1"


def _is_live_listing(row: dict) -> bool:
    import sys as _sys

    _shared = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
    if _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from listing_filters import is_live_listing  # noqa: E402

    return is_live_listing(row)


def _is_demo_catalog(row: dict) -> bool:
    import sys as _sys

    _shared = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
    if _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from listing_filters import is_demo_catalog_listing  # noqa: E402

    return is_demo_catalog_listing(row)


def _canonicalize_listing_row(row: dict) -> dict:
    import sys as _sys

    _shared = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "_shared"))
    if _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from listing_filters import canonicalize_listing_row  # noqa: E402

    return canonicalize_listing_row(row)


def _canonicalize_listing_rows(rows: list) -> list:
    return [_canonicalize_listing_row(r) for r in rows]


def _prefer_live_listings(
    rows: list[dict],
    *,
    listing_mode: str | None = None,
) -> list[dict]:
    if os.getenv("LISTINGS_PREFER_LIVE", "1").strip().lower() in {"0", "false", "no"}:
        return rows
    live = [r for r in rows if _is_live_listing(r)]
    mode = (listing_mode or "").strip().lower()
    if mode == "rent":
        live_rent = [
            r for r in live
            if str(r.get("listing_mode") or "sale").lower() == "rent"
        ]
        if live_rent:
            return live_rent
        # Portal rent loaded globally — do not fall back to stage seed.
        if _has_live_rent_catalog():
            return []
        if os.getenv("LISTINGS_SUPPRESS_DEMO_RENT", "0").strip().lower() in {
            "1", "true", "yes",
        }:
            return []
        rent_rows = [
            r for r in rows
            if str(r.get("listing_mode") or "sale").lower() == "rent"
        ]
        return rent_rows if rent_rows else rows
    if not live:
        return rows
    return live


# ----------------------- Storage adapter (Postgres) -----------------------
class _PostgresStore:
    """Thin psycopg adapter. Uses a single autocommit connection for the demo;
    production swaps in a pool (psycopg_pool)."""
    def __init__(self, url: str):
        import psycopg  # type: ignore
        from psycopg.rows import dict_row  # type: ignore
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.url = url
        self._migrate()
        self._seed_if_empty()
        from market_cache import load_market_cache

        load_market_cache(self)

    def _connect(self):
        return self._psycopg.connect(self.url, autocommit=True,
                                     row_factory=self._dict_row)

    def _migrate(self):
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                """)
                cur.execute("SELECT version FROM schema_migrations")
                applied = {r["version"] for r in cur.fetchall()}
            files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "V*.sql")))
            for path in files:
                version = os.path.basename(path).split("__")[0]
                if version in applied:
                    continue
                with open(path) as f:
                    sql = f.read()
                with c.cursor() as cur:
                    cur.execute(sql)
                    cur.execute("INSERT INTO schema_migrations(version) VALUES (%s)",
                                (version,))
                print(f"[listings] applied migration {version}")

    def _seed_if_empty(self):
        if not os.path.exists(SEED_PATH):
            return
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM listings")
                if cur.fetchone()["n"] > 0:
                    return
            with open(SEED_PATH) as f:
                rows = json.load(f)
            with c.cursor() as cur:
                for r in rows:
                    cur.execute("""
                        INSERT INTO listings(id, data, state, type, beds, price_mxn, status,
                                             lat, lng, geo_source)
                        VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                    """, (
                        r["id"], json.dumps(r), r.get("state"), r.get("type"),
                        r.get("beds"), r.get("price_mxn"),
                        r.get("status", "available"),
                        _extract_geo(r)[0], _extract_geo(r)[1], _extract_geo(r)[2],
                    ))
            print(f"[listings] seeded {len(rows)} records")

    def list(self, state=None, type=None, city=None, beds_min=None, price_max_mxn=None,
             listing_mode=None, limit=50, *, balance_sources: bool = False):
        where = "WHERE status='available'"
        params: list = []
        if state:
            where += " AND state=%s"; params.append(state)
        if city:
            where += " AND LOWER(TRIM(COALESCE(data->>'city', ''))) = LOWER(TRIM(%s))"
            params.append(city)
        if type:
            where += " AND type=%s"; params.append(type)
        if beds_min is not None:
            where += " AND beds >= %s"; params.append(beds_min)
        if price_max_mxn is not None:
            where += " AND price_mxn <= %s"; params.append(price_max_mxn)
        if listing_mode:
            where += " AND COALESCE(data->>'listing_mode', 'sale') = %s"
            params.append(listing_mode.strip().lower())
        per_source = max(40, min(250, int(limit) // 5))
        if balance_sources:
            q = f"""
                SELECT data FROM (
                    SELECT data, updated_at, id,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(NULLIF(data->>'source', ''), 'unknown')
                               ORDER BY updated_at DESC, id
                           ) AS rn
                    FROM listings
                    {where}
                ) ranked
                WHERE rn <= %s
                ORDER BY updated_at DESC, id
                LIMIT %s
            """
            params.extend([per_source, limit])
        else:
            q = f"SELECT data FROM listings {where} ORDER BY updated_at DESC, id LIMIT %s"
            params.append(limit)
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute(q, params)
                rows = [r["data"] for r in cur.fetchall()]
        return _filter_listing_mode(_canonicalize_listing_rows(rows), listing_mode)

    def get(self, lid):
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT data FROM listings WHERE id=%s", (lid,))
                row = cur.fetchone()
        data = row["data"] if row else None
        return _canonicalize_listing_row(data) if data else None

    def put(self, lid, data):
        data = _canonicalize_listing_row(data)
        _note_live_rent_row(data)
        lat_f, lng_f, geo_src = _extract_geo(data)
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("""
                        INSERT INTO listings(id, data, state, type, beds, price_mxn, status,
                                             lat, lng, geo_source)
                        VALUES (%s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            data       = EXCLUDED.data,
                            state      = EXCLUDED.state,
                            type       = EXCLUDED.type,
                            beds       = EXCLUDED.beds,
                            price_mxn  = EXCLUDED.price_mxn,
                            status     = EXCLUDED.status,
                            lat        = EXCLUDED.lat,
                            lng        = EXCLUDED.lng,
                            geo_source = EXCLUDED.geo_source,
                            updated_at = NOW()
                    """, (
                        lid, json.dumps(data), data.get("state"), data.get("type"),
                        data.get("beds"), data.get("price_mxn"),
                        data.get("status", "available"),
                        lat_f, lng_f, geo_src,
                    ))

    def delete(self, lid: str) -> bool:
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM listings WHERE id=%s", (lid,))
                return cur.rowcount > 0

    def stats(self):
        with self._connect() as c:
            with c.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM listings")
                total = cur.fetchone()["n"]
                cur.execute("SELECT state, COUNT(*) AS n FROM listings GROUP BY state")
                by_state = {r["state"]: r["n"] for r in cur.fetchall()}
                cur.execute("SELECT type, COUNT(*) AS n FROM listings GROUP BY type")
                by_type = {r["type"]: r["n"] for r in cur.fetchall()}
        return {"total": total, "by_state": by_state, "by_type": by_type}


# ----------------------- Storage adapter (SQLite fallback) -----------------------
class _SQLiteStore:
    """In-place fallback when POSTGRES_URL is unset. Same surface as _PostgresStore."""
    def __init__(self):
        import sqlite3
        self._sqlite3 = sqlite3
        self.path = os.getenv("DB_PATH", "/app/db/listings.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._init()
        self._seed_if_empty()
        from market_cache import load_market_cache

        load_market_cache(self)

    def _c(self):
        c = self._sqlite3.connect(self.path)
        c.row_factory = self._sqlite3.Row
        return c

    def _init(self):
        with self._c() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL,
                    state TEXT, type TEXT, beds INT, price_mxn INT, status TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_listings_state ON listings(state);
                CREATE INDEX IF NOT EXISTS ix_listings_type  ON listings(type);
                CREATE INDEX IF NOT EXISTS ix_listings_price ON listings(price_mxn);
            """)

    def _seed_if_empty(self):
        if not os.path.exists(SEED_PATH):
            return
        with self._c() as c:
            cnt = c.execute("SELECT COUNT(*) AS n FROM listings").fetchone()["n"]
            if cnt > 0:
                return
            with open(SEED_PATH) as f:
                rows = json.load(f)
            for r in rows:
                c.execute(
                    "INSERT INTO listings(id, data, state, type, beds, price_mxn, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["id"], json.dumps(r), r.get("state"), r.get("type"),
                     r.get("beds"), r.get("price_mxn"), r.get("status", "available"))
                )
            print(f"[listings] seeded {len(rows)} records (sqlite fallback)")

    def list(self, state=None, type=None, city=None, beds_min=None, price_max_mxn=None,
             listing_mode=None, limit=50, *, balance_sources: bool = False):
        where = "WHERE status='available'"
        params: list = []
        if state:         where += " AND state=?";       params.append(state)
        if city:
            where += " AND LOWER(TRIM(COALESCE(json_extract(data, '$.city'), ''))) = LOWER(TRIM(?))"
            params.append(city)
        if type:          where += " AND type=?";        params.append(type)
        if beds_min is not None:
                          where += " AND beds >= ?";     params.append(beds_min)
        if price_max_mxn is not None:
                          where += " AND price_mxn <= ?"; params.append(price_max_mxn)
        if listing_mode:
                          where += " AND COALESCE(json_extract(data, '$.listing_mode'), 'sale') = ?"
                          params.append(listing_mode.strip().lower())
        per_source = max(40, min(250, int(limit) // 5))
        if balance_sources:
            q = f"""
                SELECT data FROM (
                    SELECT data,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(NULLIF(json_extract(data, '$.source'), ''), 'unknown')
                               ORDER BY id
                           ) AS rn
                    FROM listings
                    {where}
                )
                WHERE rn <= ?
                LIMIT ?
            """
            params.extend([per_source, limit])
        else:
            q = f"SELECT data FROM listings {where} LIMIT ?"
            params.append(limit)
        with self._c() as c:
            rows = [json.loads(r["data"]) for r in c.execute(q, params).fetchall()]
        return _filter_listing_mode(_canonicalize_listing_rows(rows), listing_mode)

    def get(self, lid):
        with self._c() as c:
            row = c.execute("SELECT data FROM listings WHERE id=?", (lid,)).fetchone()
        if not row:
            return None
        return _canonicalize_listing_row(json.loads(row["data"]))

    def put(self, lid, data):
        data = _canonicalize_listing_row(data)
        _note_live_rent_row(data)
        with self._c() as c:
            c.execute("""INSERT INTO listings(id, data, state, type, beds, price_mxn, status)
                         VALUES (?, ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(id) DO UPDATE SET data=excluded.data""",
                      (lid, json.dumps(data), data.get("state"), data.get("type"),
                       data.get("beds"), data.get("price_mxn"),
                       data.get("status", "available")))

    def delete(self, lid: str) -> bool:
        with self._c() as c:
            cur = c.execute("DELETE FROM listings WHERE id=?", (lid,))
            return cur.rowcount > 0

    def stats(self):
        with self._c() as c:
            total = c.execute("SELECT COUNT(*) AS n FROM listings").fetchone()["n"]
            by_state = c.execute("SELECT state, COUNT(*) AS n FROM listings GROUP BY state").fetchall()
            by_type = c.execute("SELECT type, COUNT(*) AS n FROM listings GROUP BY type").fetchall()
        return {
            "total": total,
            "by_state": {r["state"]: r["n"] for r in by_state},
            "by_type": {r["type"]: r["n"] for r in by_type},
        }


def _extract_geo(data: dict) -> tuple[float | None, float | None, str | None]:
    try:
        lat = data.get("lat")
        lng = data.get("lng")
        lat_f = float(lat) if lat is not None else None
        lng_f = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lat_f, lng_f = None, None
    src = data.get("geo_source")
    return lat_f, lng_f, str(src) if src else None


def _filter_listing_mode(rows: list[dict], want_mode: str | None) -> list[dict]:
    if not want_mode:
        return rows
    mode = want_mode.strip().lower()
    if mode == "rent":
        return [
            r for r in rows
            if str(r.get("listing_mode") or "sale").lower() == "rent"
        ]
    if mode == "sale":
        return [
            r for r in rows
            if str(r.get("listing_mode") or "sale").lower() == "sale"
        ]
    return rows


# ----------------------- Bootstrap -----------------------
def _make_store():
    if POSTGRES_URL:
        try:
            return _PostgresStore(POSTGRES_URL), "postgres"
        except Exception as e:
            print(f"[listings] Postgres unavailable, falling back to SQLite: {e}")
    return _SQLiteStore(), "sqlite"


store, store_mode = _make_store()

app = FastAPI(title="Casa·Orquesta Listings", version="0.1.0")


class Listing(BaseModel):
    id: str
    data: dict


@app.get("/health")
def health():
    return {"status": "ok", "store_mode": store_mode}


from nearby import filter_nearby, geo_coverage  # noqa: E402


@app.get("/listings/nearby")
def listings_nearby(
    lat: float,
    lng: float,
    radius_km: float = 5.0,
    state: Optional[str] = None,
    limit: int = 30,
):
    """Listings within radius_km of a point, sorted nearest first."""
    radius_km = min(max(radius_km, 0.1), 50.0)
    limit = min(max(limit, 1), 100)
    rows = store.list(state=state, limit=500)
    rows = _prefer_live_listings(rows)
    return filter_nearby(rows, lat=lat, lng=lng, radius_km=radius_km, limit=limit)


@app.get("/stats/geo")
def stats_geo():
    rows = store.list(limit=10_000)
    rows = _prefer_live_listings(rows)
    return geo_coverage(rows)


@app.get("/listings")
def list_listings(state: Optional[str] = None, type: Optional[str] = None,
                  city: Optional[str] = None,
                  beds_min: Optional[int] = None, price_max_mxn: Optional[int] = None,
                  listing_mode: Optional[str] = None, limit: int = 50,
                  balance_sources: bool = False):
    rows = store.list(state=state, type=type, city=city, beds_min=beds_min,
                      price_max_mxn=price_max_mxn, listing_mode=listing_mode,
                      limit=limit, balance_sources=balance_sources)
    return _prefer_live_listings(rows, listing_mode=listing_mode)


@app.get("/listings/{lid}")
def get_listing(lid: str):
    data = store.get(lid)
    if data is None:
        raise HTTPException(404, "listing not found")
    return data


@app.post("/listings")
def create_listing(item: Listing):
    store.put(item.id, item.data)
    return {"ok": True, "id": item.id}


@app.delete("/listings/{lid}")
def delete_listing(lid: str):
    if not store.delete(lid):
        raise HTTPException(404, "listing not found")
    return {"ok": True, "deleted": lid}


@app.get("/stats")
def stats():
    return store.stats()


# ============================================================================
# Phase 4.6 — Cross-service auth + DSAR wiring
# ============================================================================
import os as _os
import sys as _sys                                  # noqa: E402
_SHARED = _os.path.normpath(
    _os.path.join(_os.path.dirname(__file__), "..", "_shared")
)
if _SHARED not in _sys.path:
    _sys.path.insert(0, _SHARED)

try:
    from auth_middleware import AuthInjector                  # noqa: E402
    from dsar_responder import mount_dsar                     # noqa: E402
    _DSAR_READY = True
except Exception as _e:                                       # pragma: no cover
    print(f"[listings] DSAR wiring deferred: {_e!r}")
    _DSAR_READY = False


# ============================================================================
# Phase 4.9 — Favorites + saved searches (user-indexed; Postgres in prod,
#              in-memory in dev). DSAR gather/purge now actually export and
#              delete real rows.
# ============================================================================
from store import (                                            # noqa: E402
    build_default_store,
)
from pydantic import BaseModel as _BaseModel                   # noqa: E402

_user_store = build_default_store()


def set_user_store(store) -> None:
    """Test hook — inject the in-memory store with pinned now/id."""
    global _user_store
    _user_store = store


def get_user_store():
    return _user_store


class FavoriteCreate(_BaseModel):
    tenant_id: str
    user_id: str
    listing_id: str
    note: str = ""


@app.post("/users/{user_id}/favorites")
async def add_favorite(user_id: str, req: FavoriteCreate) -> dict:
    if req.user_id != user_id:
        raise HTTPException(status_code=400, detail="user_id mismatch")
    f = await _user_store.add_favorite(
        tenant_id=req.tenant_id, user_id=req.user_id,
        listing_id=req.listing_id, note=req.note,
    )
    return f.public()


@app.delete("/users/{user_id}/favorites/{listing_id}")
async def remove_favorite(user_id: str, listing_id: str,
                          tenant_id: str) -> dict:
    n = await _user_store.remove_favorite(
        tenant_id=tenant_id, user_id=user_id, listing_id=listing_id,
    )
    return {"removed": n}


@app.get("/users/{user_id}/favorites")
async def list_favorites(user_id: str, tenant_id: str) -> dict:
    favs = await _user_store.list_favorites(
        tenant_id=tenant_id, user_id=user_id,
    )
    return {"count": len(favs), "favorites": [f.public() for f in favs]}


class SavedSearchCreate(_BaseModel):
    tenant_id: str
    user_id: str
    label: str = ""
    query_text: str
    filters: dict = {}


@app.post("/users/{user_id}/searches")
async def add_search(user_id: str, req: SavedSearchCreate) -> dict:
    if req.user_id != user_id:
        raise HTTPException(status_code=400, detail="user_id mismatch")
    s = await _user_store.add_search(
        tenant_id=req.tenant_id, user_id=req.user_id,
        label=req.label, query_text=req.query_text,
        filters=dict(req.filters),
    )
    return s.public()


@app.get("/users/{user_id}/searches")
async def list_searches(user_id: str, tenant_id: str) -> dict:
    searches = await _user_store.list_searches(
        tenant_id=tenant_id, user_id=user_id,
    )
    return {"count": len(searches),
            "saved_searches": [s.public() for s in searches]}


async def _listings_dsar_gather(user_id: str, tenant_id: str) -> dict:
    """Now returns real rows from `favorites` + `saved_searches`."""
    return await _user_store.gather_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


async def _listings_dsar_purge(user_id: str, tenant_id: str) -> dict:
    """Hard-delete the user's favorites + searches. No fiscal hold."""
    return await _user_store.purge_user_data(
        tenant_id=tenant_id, user_id=user_id,
    )


if _DSAR_READY:
    app.add_middleware(AuthInjector)
    mount_dsar(
        app,
        gather=_listings_dsar_gather,
        purge=_listings_dsar_purge,
        service_name="listings",
    )
