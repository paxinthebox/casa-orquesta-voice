-- V002: denormalized coordinates for nearby / map queries (Phase 6).
-- Lat/lng remain canonical in data JSONB; columns speed radius filters.

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS geo_source TEXT;

UPDATE listings
SET
    lat = NULLIF(data->>'lat', '')::double precision,
    lng = NULLIF(data->>'lng', '')::double precision,
    geo_source = COALESCE(data->>'geo_source', 'portal')
WHERE lat IS NULL
  AND data->>'lat' IS NOT NULL
  AND data->>'lng' IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_listings_geo
    ON listings (lat, lng)
    WHERE lat IS NOT NULL AND lng IS NOT NULL AND status = 'available';
