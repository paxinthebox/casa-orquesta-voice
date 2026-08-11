-- Phase 6: multi-stop property tours (scheduling service).
-- Applied manually or via ops migration runner before P6.2 ships.

CREATE TABLE IF NOT EXISTS tours (
    id                  TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL,
    buyer_id            TEXT        NOT NULL,
    agent_id            TEXT,
    status              TEXT        NOT NULL DEFAULT 'draft'
                                    CHECK (status IN (
                                        'draft', 'proposed', 'confirmed',
                                        'completed', 'cancelled'
                                    )),
    origin              JSONB       NOT NULL DEFAULT '{}'::jsonb,
    listing_ids         JSONB       NOT NULL DEFAULT '[]'::jsonb,
    ordered_listing_ids JSONB       NOT NULL DEFAULT '[]'::jsonb,
    legs                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    proposed_slots      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    selected_slot       JSONB,
    dwell_minutes       INT         NOT NULL DEFAULT 45,
    starts_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);

ALTER TABLE visits
    ADD COLUMN IF NOT EXISTS tour_id TEXT REFERENCES tours(id);

CREATE INDEX IF NOT EXISTS idx_tours_buyer
    ON tours(tenant_id, buyer_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_tours_upcoming
    ON tours(tenant_id, starts_at)
    WHERE deleted_at IS NULL AND status IN ('proposed', 'confirmed');

CREATE INDEX IF NOT EXISTS idx_visits_tour
    ON visits(tour_id)
    WHERE tour_id IS NOT NULL;
