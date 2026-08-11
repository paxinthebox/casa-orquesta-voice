-- ---------------------------------------------------------------------------
-- Identity service — initial schema (Phase 4.1)
--
-- Postgres 15+. Designed for row-level security (RLS) on tenant_id —
-- every row belonging to a tenant carries the tenant_id column, and
-- the per-service Postgres role applies an RLS policy that injects
-- the JWT's `tenant_id` claim via `current_setting('app.tenant_id')`.
--
-- Tables:
--   tenants            — top-level multi-tenant boundary
--   users              — phone-OTP-authenticated end users + brokers
--   consent_records    — every consent grant / revoke (hash-chained)
--   sessions           — active JWT-backed sessions (one per device)
--   otp_challenges     — short-lived OTP challenges (no PII at rest)
--   audit_log          — hash-chained audit trail (matches the shape
--                        used by services/orchestrator/hooks/audit.py)
--
-- All ids are opaque strings (`tnt_…`, `u_…`, `cns_…`, `sess_…`, `otp_…`)
-- so we never expose autoincrement sequences to clients.
--
-- This file is read by Flyway / sqlx-migrate / the Phase 4.5 ops setup.
-- For the standalone test runner we use the in-memory store; the
-- structural sanity gate validates the SQL parses but does not execute
-- it against a live Postgres instance.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- tenants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tenants (
    id             TEXT        PRIMARY KEY,
    name           TEXT        NOT NULL,
    country        TEXT        NOT NULL DEFAULT 'MX',
    plan           TEXT        NOT NULL DEFAULT 'pilot',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Pilot tenant for CDMX + Morelos beta.
INSERT INTO tenants (id, name, country, plan)
VALUES ('tnt_pilot_mx', 'Casa·Orquesta Pilot', 'MX', 'pilot')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- users
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id              TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    phone_e164      TEXT        NOT NULL,
    role            TEXT        NOT NULL DEFAULT 'buyer'
                                CHECK (role IN ('buyer', 'broker', 'admin')),
    display_name    TEXT,
    locale          TEXT        NOT NULL DEFAULT 'es-MX',
    auth0_subject   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at   TIMESTAMPTZ,
    deleted_at      TIMESTAMPTZ,
    UNIQUE (tenant_id, phone_e164)
);

CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_phone     ON users(phone_e164);
CREATE INDEX IF NOT EXISTS idx_users_auth0_sub ON users(auth0_subject);

-- ---------------------------------------------------------------------------
-- consent_records — hash-chained (each row has prev_hash + content_hash)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consent_records (
    id              TEXT        PRIMARY KEY,
    user_id         TEXT        NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    tenant_id       TEXT        NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    purpose         TEXT        NOT NULL,
    granted         BOOLEAN     NOT NULL,
    text_hash       TEXT        NOT NULL,  -- SHA-256 of the aviso text version
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ,
    prev_hash       TEXT,                  -- chain link
    content_hash    TEXT        NOT NULL   -- sha256(prev_hash || row data)
);

CREATE INDEX IF NOT EXISTS idx_consent_user    ON consent_records(user_id);
CREATE INDEX IF NOT EXISTS idx_consent_tenant  ON consent_records(tenant_id);
CREATE INDEX IF NOT EXISTS idx_consent_purpose ON consent_records(user_id, purpose);

-- ---------------------------------------------------------------------------
-- sessions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT        PRIMARY KEY,
    user_id         TEXT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_agent      TEXT,
    device_id       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_active
    ON sessions(user_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- otp_challenges — short-lived; we only store the hash of the code
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS otp_challenges (
    id              TEXT        PRIMARY KEY,
    phone_e164      TEXT        NOT NULL,
    code_hash       TEXT        NOT NULL,  -- SHA-256(code)
    attempts        INTEGER     NOT NULL DEFAULT 0,
    expires_at      TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_otp_phone_expires
    ON otp_challenges(phone_e164, expires_at);

-- Auto-clean expired challenges nightly (cron handled in P4.5 ops).
CREATE OR REPLACE FUNCTION purge_expired_otp() RETURNS void AS $$
    DELETE FROM otp_challenges
     WHERE expires_at < now() - INTERVAL '1 hour';
$$ LANGUAGE sql;

-- ---------------------------------------------------------------------------
-- invite_codes — Phase 4.4 closed-beta access tokens
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invite_codes (
    code                    TEXT        PRIMARY KEY,    -- 'XXXX-XXXX'
    tenant_id               TEXT        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    label                   TEXT        NOT NULL,        -- ops-friendly label
    role                    TEXT        NOT NULL DEFAULT 'buyer'
                                        CHECK (role IN ('buyer','broker','admin')),
    notes                   TEXT        NOT NULL DEFAULT '',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at              TIMESTAMPTZ,
    redeemed_at             TIMESTAMPTZ,
    redeemed_by_user_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
    redeemed_by_phone       TEXT
);

CREATE INDEX IF NOT EXISTS idx_invite_codes_tenant_label
    ON invite_codes(tenant_id, label);
CREATE INDEX IF NOT EXISTS idx_invite_codes_open
    ON invite_codes(tenant_id) WHERE redeemed_at IS NULL;

-- ---------------------------------------------------------------------------
-- audit_log — hash-chained, mirrors services/orchestrator/hooks/audit.py
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL   PRIMARY KEY,
    tenant_id       TEXT,
    user_id         TEXT,
    actor           TEXT        NOT NULL,            -- 'identity' | 'orchestrator' | …
    action          TEXT        NOT NULL,            -- 'auth.start' | 'auth.verify' | …
    target          TEXT,                            -- e.g. user id, session id
    detail          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    prev_hash       TEXT,
    content_hash    TEXT        NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts
    ON audit_log(tenant_id, ts);
CREATE INDEX IF NOT EXISTS idx_audit_user_ts
    ON audit_log(user_id, ts);

-- ---------------------------------------------------------------------------
-- Row-level security stubs — actual policies enabled in P4.5 ops.
-- ---------------------------------------------------------------------------
ALTER TABLE users           ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions        ENABLE ROW LEVEL SECURITY;

-- A permissive policy until per-tenant Postgres roles are provisioned.
-- The service account that runs migrations is the only role that
-- bypasses RLS; per-tenant roles enforce tenant_id = current_setting.
CREATE POLICY users_self_tenant ON users
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY consent_self_tenant ON consent_records
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY sessions_self_tenant ON sessions
    USING (tenant_id = current_setting('app.tenant_id', true));
