-- ---------------------------------------------------------------------------
-- Listings service — initial schema (Phase 4.9)
--
-- The MVP's `listings` table is the canonical property catalog (public
-- reference data — not user-indexed). This migration adds the two
-- user-indexed tables P4.6 explicitly flagged as missing:
--
--   favorites      — (tenant_id, user_id, listing_id) join
--   saved_searches — natural-language + structured filter triples a
--                    user wants re-run periodically
--
-- Both are tenant-scoped with RLS (placeholder until per-tenant
-- Postgres roles ship in the ops sprint). DSAR purge for these tables
-- is a real DELETE — they hold no fiscal records.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- favorites — user-pinned listings
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS favorites (
    id                TEXT        PRIMARY KEY,           -- 'fav_…'
    tenant_id         TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    listing_id        TEXT        NOT NULL,              -- references listings(id) in MVP
    note              TEXT        NOT NULL DEFAULT '',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, user_id, listing_id)
);

CREATE INDEX IF NOT EXISTS idx_favorites_user
    ON favorites(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_favorites_listing
    ON favorites(listing_id);

-- ---------------------------------------------------------------------------
-- saved_searches — text query + structured filters
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS saved_searches (
    id                TEXT        PRIMARY KEY,           -- 'srch_…'
    tenant_id         TEXT        NOT NULL,
    user_id           TEXT        NOT NULL,
    label             TEXT        NOT NULL DEFAULT '',   -- user-friendly name
    query_text        TEXT        NOT NULL,              -- the original voice/text query
    filters           JSONB       NOT NULL DEFAULT '{}'::jsonb,
                                  -- e.g. {"city":"CDMX","beds_min":2,"price_max_mxn":7000000}
    last_run_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_saved_searches_user
    ON saved_searches(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_saved_searches_filters
    ON saved_searches USING gin (filters);

-- ---------------------------------------------------------------------------
-- RLS — placeholder policies; per-tenant roles arrive in ops.
-- ---------------------------------------------------------------------------
ALTER TABLE favorites      ENABLE ROW LEVEL SECURITY;
ALTER TABLE saved_searches ENABLE ROW LEVEL SECURITY;

CREATE POLICY favorites_tenant_scope ON favorites
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY saved_searches_tenant_scope ON saved_searches
    USING (tenant_id = current_setting('app.tenant_id', true));
