-- ---------------------------------------------------------------------------
-- Scheduling service — initial schema (Phase 4.9)
--
-- Backs the in-memory `VisitStore` from P1.4 with a real table. Hard-
-- delete capable — visits hold no fiscal records, so DSAR purge can
-- DELETE the row instead of leaving a tombstone (which is what P4.6
-- did as a workaround). The structured-time columns let us index on
-- (tenant_id, starts_at) for the "what's coming up this week" view.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS visits (
    id                TEXT        PRIMARY KEY,            -- 'V-…'
    tenant_id         TEXT        NOT NULL,
    listing_id        TEXT        NOT NULL,
    buyer_id          TEXT        NOT NULL,               -- user_id who requested
    agent_id          TEXT,                                -- assigned broker
    status            TEXT        NOT NULL DEFAULT 'proposed'
                                  CHECK (status IN (
                                      'proposed','confirmed','declined',
                                      'completed','cancelled'
                                  )),
    proposed_slots    JSONB       NOT NULL DEFAULT '[]'::jsonb,
                                  -- array of {start_iso, end_iso}
    selected_slot     JSONB,
    starts_at         TIMESTAMPTZ,                         -- denormalized from selected_slot
    feedback          JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at        TIMESTAMPTZ                          -- DSAR soft-delete; nightly hard-purge
);

CREATE INDEX IF NOT EXISTS idx_visits_buyer
    ON visits(tenant_id, buyer_id);
CREATE INDEX IF NOT EXISTS idx_visits_listing
    ON visits(listing_id);
CREATE INDEX IF NOT EXISTS idx_visits_upcoming
    ON visits(tenant_id, starts_at)
    WHERE deleted_at IS NULL AND status IN ('proposed','confirmed');

ALTER TABLE visits ENABLE ROW LEVEL SECURITY;
CREATE POLICY visits_tenant_scope ON visits
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Nightly cron purges soft-deleted rows older than 30 days.
CREATE OR REPLACE FUNCTION purge_deleted_visits() RETURNS integer AS $$
DECLARE n integer;
BEGIN
    DELETE FROM visits
     WHERE deleted_at IS NOT NULL
       AND deleted_at < now() - INTERVAL '30 days'
    RETURNING 1 INTO n;
    RETURN coalesce(n, 0);
END;
$$ LANGUAGE plpgsql;
