"""
Shared Postgres connection helpers — Phase 4.9.

Goals:
  1. Every per-service store imports the *same* pool helper, so the
     deployed services all use one connection-pool implementation
     (currently asyncpg). Swapping engines is a one-file change.
  2. Tests stay hermetic — `Store` is a Protocol; the in-memory
     variant each service ships satisfies the same interface as the
     Postgres-backed adapter. Test runners use the in-memory variant
     directly, never reaching for the pool.
  3. The pool is lazy. Importing this module costs nothing; only the
     first `await get_pool(...)` actually opens connections. That keeps
     `import main` cheap in the test sandbox where asyncpg isn't
     installed.

Public API:

    from db import get_pool, with_conn, run_migrations

    pool = await get_pool()                       # lazy + cached
    async with with_conn() as conn:
        rows = await conn.fetch("SELECT 1")

    await run_migrations(service_dir="services/listings")

Migrations are tracked in a `schema_migrations` table the helper
provisions on first run. Each service ships its own `migrations/` dir
with `0001_init.sql`, `0002_...`, etc. The runner is intentionally
tiny — Flyway is overkill for the seven services we have.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional, Protocol

POSTGRES_URL = os.getenv("POSTGRES_URL", "")
MAX_POOL_SIZE = int(os.getenv("CO_DB_MAX_POOL_SIZE", "10"))
MIN_POOL_SIZE = int(os.getenv("CO_DB_MIN_POOL_SIZE", "1"))


# ---------------------------------------------------------------------------
# Connection / pool protocols (asyncpg-shaped surface; test fakes
# implement the same shape)
# ---------------------------------------------------------------------------
class _Conn(Protocol):
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetch(self, query: str, *args: Any) -> list: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...
    async def fetchval(self, query: str, *args: Any) -> Any: ...
    async def close(self) -> None: ...


class _Pool(Protocol):
    def acquire(self) -> "_AcquireCtx": ...
    async def close(self) -> None: ...


class _AcquireCtx(Protocol):
    async def __aenter__(self) -> _Conn: ...
    async def __aexit__(self, *a) -> None: ...


# ---------------------------------------------------------------------------
# Pool singleton (lazy)
# ---------------------------------------------------------------------------
_pool: Optional[_Pool] = None


async def get_pool() -> _Pool:
    """Return a process-wide asyncpg pool. Lazy + cached."""
    global _pool
    if _pool is not None:
        return _pool
    if not POSTGRES_URL:
        raise RuntimeError(
            "POSTGRES_URL not set — refusing to open a real pool. "
            "Tests should use the in-memory store fallback or inject a fake."
        )
    import asyncpg                                              # type: ignore
    _pool = await asyncpg.create_pool(
        dsn=POSTGRES_URL,
        min_size=MIN_POOL_SIZE,
        max_size=MAX_POOL_SIZE,
        command_timeout=15.0,
    )
    return _pool


def set_pool(pool: _Pool) -> None:
    """Test hook — inject a deterministic fake pool."""
    global _pool
    _pool = pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
class _WithConn:
    """`async with with_conn() as conn:` — acquires from the pool."""

    def __init__(self):
        self._pool: Optional[_Pool] = None
        self._ctx = None
        self._conn: Optional[_Conn] = None

    async def __aenter__(self) -> _Conn:
        self._pool = await get_pool()
        self._ctx = self._pool.acquire()
        self._conn = await self._ctx.__aenter__()
        return self._conn       # type: ignore[return-value]

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ctx is not None:
            await self._ctx.__aexit__(exc_type, exc, tb)


def with_conn() -> _WithConn:
    return _WithConn()


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------
SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    service      TEXT        NOT NULL,
    version      TEXT        NOT NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    sha256       TEXT        NOT NULL,
    PRIMARY KEY (service, version)
);
"""

MIGRATION_FILE_RE = re.compile(r"^(\d{4})_.+\.sql$")


async def run_migrations(
    *, service_name: str, migrations_dir: Path,
) -> list[str]:
    """
    Apply every `NNNN_*.sql` in `migrations_dir` that hasn't been
    applied yet (according to `schema_migrations`). Returns the list
    of versions that were applied in this run.

    Each migration is run inside its own transaction so a failure
    rolls the file back without taking down the others.
    """
    import hashlib

    files = sorted(
        p for p in migrations_dir.iterdir()
        if p.is_file() and MIGRATION_FILE_RE.match(p.name)
    )
    if not files:
        return []
    applied: list[str] = []
    async with with_conn() as conn:
        await conn.execute(SCHEMA_MIGRATIONS_DDL)
        for path in files:
            version = path.name
            already = await conn.fetchval(
                "SELECT 1 FROM schema_migrations "
                "WHERE service = $1 AND version = $2",
                service_name, version,
            )
            if already:
                continue
            sql = path.read_text(encoding="utf-8")
            sha = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            # Run inside a savepoint so we can keep the outer connection
            # for the next iteration on failure.
            await conn.execute("BEGIN")
            try:
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations "
                    "(service, version, sha256) VALUES ($1, $2, $3)",
                    service_name, version, sha,
                )
                await conn.execute("COMMIT")
                applied.append(version)
            except Exception:
                await conn.execute("ROLLBACK")
                raise
    return applied


# ---------------------------------------------------------------------------
# Service-store Protocol
# ---------------------------------------------------------------------------
# Each per-service `store.py` exposes a class that implements this
# interface against either Postgres or an in-memory dict. Tests + the
# dev path use the in-memory one; production wires the Postgres one.
class StoreLike(Protocol):
    """Minimal surface every service store satisfies. Concrete services
    extend with their domain-specific methods (e.g. `add_favorite`)."""

    async def health(self) -> dict: ...
    async def close(self) -> None: ...
