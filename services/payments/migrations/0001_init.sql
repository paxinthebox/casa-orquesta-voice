-- ---------------------------------------------------------------------------
-- Payments + CFDI service — initial schema (Phase 4.9)
--
-- Two tables: payments (Stripe/Conekta intent + Mexico-domestic SPEI)
-- and cfdis (SAT-emitted XML records). CFDI 4.0 and SAT retention
-- rules both require fiscal records to be kept for 5 years; DSAR
-- purge can only DELETE unfunded intents, otherwise it has to leave
-- the row and null the PII columns.
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- payments — intent + capture lifecycle
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
    id                TEXT        PRIMARY KEY,            -- 'pay_…'
    tenant_id         TEXT        NOT NULL,
    payer_id          TEXT,                                -- PII; nullable for redacted records
    payee_id          TEXT,
    amount_mxn        NUMERIC(14,2) NOT NULL,
    description       TEXT        NOT NULL DEFAULT '',
    method            TEXT        NOT NULL DEFAULT 'card'
                                  CHECK (method IN ('card','spei','oxxo')),
    status            TEXT        NOT NULL DEFAULT 'intent'
                                  CHECK (status IN (
                                      'intent','authorized','captured','failed',
                                      'refunded','cancelled'
                                  )),
    provider          TEXT,                                -- 'stripe'|'conekta'|'mock'
    provider_intent_id TEXT,
    metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    captured_at       TIMESTAMPTZ,
    -- SAT retention: 5 years from capture date for captured payments.
    -- Drafts/intents/failed have no hold and can be hard-deleted.
    retention_until   TIMESTAMPTZ,
    redacted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payments_payer
    ON payments(tenant_id, payer_id);
CREATE INDEX IF NOT EXISTS idx_payments_payee
    ON payments(tenant_id, payee_id);
CREATE INDEX IF NOT EXISTS idx_payments_status
    ON payments(tenant_id, status);

-- ---------------------------------------------------------------------------
-- cfdis — SAT 4.0 emitted records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cfdis (
    id                TEXT        PRIMARY KEY,            -- 'cfdi_…'
    tenant_id         TEXT        NOT NULL,
    payment_id        TEXT REFERENCES payments(id) ON DELETE SET NULL,
    uuid_fiscal       TEXT        NOT NULL UNIQUE,         -- SAT-issued
    rfc_emisor        TEXT        NOT NULL,
    rfc_receptor      TEXT        NOT NULL,                -- PII for the customer
    total_mxn         NUMERIC(14,2) NOT NULL,
    xml_blob          TEXT        NOT NULL,                -- full SAT XML
    pdf_s3_key        TEXT,                                -- s3://… path to rendered PDF
    metadata          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    emitted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 5-year retention from emit date. Hard requirement.
    retention_until   TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '5 years'),
    redacted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_cfdis_payment
    ON cfdis(payment_id);
CREATE INDEX IF NOT EXISTS idx_cfdis_receptor
    ON cfdis(tenant_id, rfc_receptor);
CREATE INDEX IF NOT EXISTS idx_cfdis_emitted
    ON cfdis(emitted_at);

ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE cfdis    ENABLE ROW LEVEL SECURITY;
CREATE POLICY payments_tenant_scope ON payments
    USING (tenant_id = current_setting('app.tenant_id', true));
CREATE POLICY cfdis_tenant_scope ON cfdis
    USING (tenant_id = current_setting('app.tenant_id', true));

-- Auto-set retention_until when a payment captures.
CREATE OR REPLACE FUNCTION payments_set_retention() RETURNS trigger AS $$
BEGIN
    IF NEW.status IN ('captured','refunded')
       AND NEW.retention_until IS NULL THEN
        NEW.retention_until := now() + INTERVAL '5 years';
        NEW.captured_at := coalesce(NEW.captured_at, now());
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_payments_retention ON payments;
CREATE TRIGGER trg_payments_retention
    BEFORE INSERT OR UPDATE OF status ON payments
    FOR EACH ROW EXECUTE FUNCTION payments_set_retention();
