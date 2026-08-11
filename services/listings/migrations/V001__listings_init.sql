-- V001: initial schema for the listings service.
-- Applied by the migration runner on service boot.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    data          JSONB NOT NULL,
    state         TEXT,
    type          TEXT,
    beds          INTEGER,
    price_mxn     BIGINT,
    status        TEXT NOT NULL DEFAULT 'available',
    embedding     vector(1024),                   -- BGE-M3 dimensions; nullable
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_listings_state     ON listings(state);
CREATE INDEX IF NOT EXISTS ix_listings_type      ON listings(type);
CREATE INDEX IF NOT EXISTS ix_listings_price     ON listings(price_mxn);
CREATE INDEX IF NOT EXISTS ix_listings_status    ON listings(status);

-- IVFFlat for approximate nearest-neighbor; lists=100 is a sane default
-- at pilot scale (≤ 10K listings). Rebuild with more lists once we exceed.
CREATE INDEX IF NOT EXISTS ix_listings_embedding
  ON listings USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- Tenant scoping (Phase 4 multi-tenant work will populate this).
ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS tenant_id TEXT;
CREATE INDEX IF NOT EXISTS ix_listings_tenant ON listings(tenant_id);
