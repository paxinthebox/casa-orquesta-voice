# Postgres + Redis on Fly.io — provisioning runbook

The founder runs these once. Not infra-as-code (Fly's Postgres is managed; Terraform support is limited as of mid-2026). Re-run only when re-bootstrapping a region.

## 1. Managed Postgres cluster

```bash
fly postgres create \
  --name casa-orquesta-pg \
  --region ord \
  --initial-cluster-size 1 \
  --vm-size shared-cpu-1x \
  --volume-size 10
```

After creation, capture the connection string from the output and store it:

```bash
fly secrets set DATABASE_URL="postgres://..." \
  --app casa-orquesta-orchestrator \
  --app casa-orquesta-identity \
  --app casa-orquesta-listings \
  --app casa-orquesta-scheduling \
  --app casa-orquesta-documents \
  --app casa-orquesta-payments
```

## 2. pgvector extension

Connect with `fly postgres connect -a casa-orquesta-pg` then:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE DATABASE casa_listings;
CREATE DATABASE casa_identity;
CREATE DATABASE casa_documents;
CREATE DATABASE langfuse;
```

(Per-service databases keep blast-radius small; share the cluster.)

## 3. Redis via Upstash (free tier sufficient for stage)

Upstash has a Fly integration that is easier than self-hosted Redis on Fly.

```bash
fly redis create --name casa-orquesta-redis --region ord --no-replicas
```

Set the URL on every service that needs it:

```bash
fly secrets set REDIS_URL="redis://..." \
  --app casa-orquesta-orchestrator \
  --app casa-orquesta-voice-gateway \
  --app casa-orquesta-scheduling
```

## 4. Tigris S3-compatible object storage

For the audit WORM bucket and document PDFs. Fly's bundled offering.

```bash
fly storage create --name casa-orquesta-audit
# Capture AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_ENDPOINT_URL_S3
```

Then:

```bash
fly secrets set \
  S3_ENDPOINT="$AWS_ENDPOINT_URL_S3" \
  S3_BUCKET="casa-orquesta-audit" \
  S3_ACCESS_KEY="$AWS_ACCESS_KEY_ID" \
  S3_SECRET_KEY="$AWS_SECRET_ACCESS_KEY" \
  --app casa-orquesta-orchestrator \
  --app casa-orquesta-documents
```

Enable object lock (WORM) on the bucket in the Tigris console — required for LFPIORPI audit retention.

## 5. Per-service Fly apps

For each service that has a `fly.<name>.toml` in this directory:

```bash
cd services/<name>
fly launch --copy-config --no-deploy --name casa-orquesta-<name> --region ord
fly deploy --config ../../infra/fly/fly.<name>.toml
```

## 6. Domain + TLS

```bash
# Done once at the gateway app:
fly certs add stage.casaorquesta.io --app casa-orquesta-gateway
# Then add the CNAME at your DNS provider per Fly's instructions.
```

## 7. Verification

```bash
fly status --app casa-orquesta-orchestrator     # expect "passing"
fly status --app casa-orquesta-voice-gateway    # expect "passing"
fly logs --app casa-orquesta-orchestrator       # expect /health 200s
curl https://casa-orquesta-orchestrator.fly.dev/health
```

## Cost shape at stage (estimated)

Per `docs/Stage_Voice_Plan.xlsx` runtime tab. Roughly **$130/mo for Fly.io** covering compute + Postgres + Redis at the stage VM sizes above. Scales down to near-zero between testing windows if you `fly machine stop` the non-essential services overnight.

## Production migration notes

When this leaves stage:

- Bump Postgres to `shared-cpu-2x` and add a replica.
- Switch voice-gateway to a `performance-cpu-2x` for lower TTS-first-byte latency.
- Move the audit bucket to a dedicated AWS S3 in Mexico region (AWS mx-central-1) for LFPDPPP data residency.
- Add Fly's Mexico region when published (`mty` or `mex` once available).
- Apply for Anthropic production tier rate limits.
