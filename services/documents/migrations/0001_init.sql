-- ---------------------------------------------------------------------------
-- Documents service — initial schema (Phase 4.9)
--
-- Generates + tracks Promesa de Compraventa documents and their
-- NOM-151 / Mifiel-style e-signature lifecycle. CFDI 4.0 art. 30
-- requires signed-document records to be retained for 5 years; the
-- `retention_until` column encodes that hold explicitly so DSAR
-- purge can DELETE drafts while only REDACTING signed contracts.
--
-- PDF files themselves live in S3/Tigris under
-- `s3://casa-documents/<tenant_id>/<doc_id>.pdf` (versioned + object
-- lock). This table stores metadata + the signed hash chain.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS documents (
    id                TEXT        PRIMARY KEY,            -- 'doc_…'
    tenant_id         TEXT        NOT NULL,
    buyer_id          TEXT        NOT NULL,               -- PII; redacted on DSAR for signed docs
    owner_id          TEXT,
    listing_id        TEXT,
    kind              TEXT        NOT NULL DEFAULT 'promesa_compraventa',
    status            TEXT        NOT NULL DEFAULT 'draft'
                                  CHECK (status IN (
                                      'draft','partially_signed','signed','void'
                                  )),
    sha256            TEXT,                                -- hash of the PDF bytes
    s3_key            TEXT,                                -- s3://… path
    signers           JSONB       NOT NULL DEFAULT '{}'::jsonb,
                                  -- {buyer: {signer_id, ts, signature_hash}, owner: {...}}
    nom151_token      TEXT,                                -- NOM-151 trusted-timestamp anchor
    metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    signed_at         TIMESTAMPTZ,
    -- CFDI 4.0 art. 30: signed contracts retained for 5 years from sign date.
    -- Drafts get NULL; deletion is unconstrained.
    retention_until   TIMESTAMPTZ,
    redacted_at       TIMESTAMPTZ                          -- set when PII fields are nulled by DSAR
);

CREATE INDEX IF NOT EXISTS idx_documents_buyer
    ON documents(tenant_id, buyer_id);
CREATE INDEX IF NOT EXISTS idx_documents_listing
    ON documents(listing_id);
CREATE INDEX IF NOT EXISTS idx_documents_status
    ON documents(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_retention
    ON documents(retention_until)
    WHERE retention_until IS NOT NULL;

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
CREATE POLICY documents_tenant_scope ON documents
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Auto-set retention_until at the moment a doc transitions to 'signed'.
CREATE OR REPLACE FUNCTION documents_set_retention() RETURNS trigger AS $$
BEGIN
    IF NEW.status = 'signed' AND NEW.retention_until IS NULL THEN
        NEW.retention_until := now() + INTERVAL '5 years';
        NEW.signed_at := coalesce(NEW.signed_at, now());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_retention ON documents;
CREATE TRIGGER trg_documents_retention
    BEFORE INSERT OR UPDATE OF status ON documents
    FOR EACH ROW EXECUTE FUNCTION documents_set_retention();
