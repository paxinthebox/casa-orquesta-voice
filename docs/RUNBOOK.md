# Casa·Orquesta · On-call Runbook

> The single page on-call reaches for at 2 AM. Service inventory,
> healthcheck URLs, dashboard links, common-incident playbooks,
> deploy/rollback procedures, database recovery.
>
> Last reviewed: 2026-06. Re-read it before your first shift. Edit it
> the moment you discover something missing — the runbook is the
> rotation's working memory, not a document.

## Table of contents

1. Quick reference card
2. Service inventory
3. Dashboards + links
4. Common incidents (playbooks)
5. Deploy + rollback
6. Database recovery
7. Secrets + rotation
8. Escalation tree

---

## 1 · Quick reference card

| What | Where |
|---|---|
| On-call rotation       | PagerDuty `casaorquesta-oncall` |
| Status page            | https://status.casaorquesta.mx |
| Public docs            | https://docs.casaorquesta.mx |
| Aviso de Privacidad    | https://casaorquesta.mx/aviso-de-privacidad |
| War room (Slack)       | `#war-room` |
| Founder escalation     | Slack `@founder`, then phone |
| Counsel (LFPDPPP)      | privacidad@casaorquesta.mx |

**Right now (during an incident):**
- Acknowledge the page within 5 minutes.
- Open `#war-room`, post the page link + a one-line state.
- Run `./scripts/verify.sh` from main if a deploy is suspected.
- If user data may be at risk, page counsel + founder before you start triage.

---

## 2 · Service inventory

| Service          | Port | Healthcheck            | Phase shipped | DSAR endpoint              |
| ---------------- | ---- | ---------------------- | ------------- | -------------------------- |
| `orchestrator`   | 8000 | `GET /health`          | P1.x          | `/dsar/user/{user_id}`     |
| `voice-gateway`  | 8001 | `GET /health`          | P2.x          | `/dsar/user/{user_id}`     |
| `identity`       | 8002 | `GET /health`          | P4.1          | (root for fan-out)         |
| `listings`       | 8003 | `GET /health`          | P1.4          | `/dsar/user/{user_id}`     |
| `scheduling`     | 8004 | `GET /health`          | P1.4          | `/dsar/user/{user_id}`     |
| `documents`      | 8005 | `GET /health`          | P1.4          | `/dsar/user/{user_id}`     |
| `payments`       | 8006 | `GET /health`          | P1.4          | `/dsar/user/{user_id}`     |
| `comms`          | 8007 | `GET /health`          | P1.4          | `/dsar/user/{user_id}`     |

**Stateful dependencies:**
- **Postgres** — every service that persists. Backups: `scripts/backup_postgres.sh` nightly to S3/Tigris with 30-day retention.
- **Redis** — scheduling visits + comms 24h window + orchestrator spend cap counters. Loss is recoverable; everything falls back to in-memory mode.
- **S3 / Tigris (WORM)** — `documents/` PDFs + nightly Postgres dumps. Versioning + object-lock enabled.

**External vendors** (each tracked in `docs/LEGAL_PACK.md` with a DPA):
Deepgram (STT) · Anthropic (LLM) · ElevenLabs (TTS) · Azure (TTS fallback) · Auth0 (phone OTP) · Sentry (telemetry) · Meta (WhatsApp Cloud) · Twilio (SMS).

---

## 3 · Dashboards + links

| Dashboard               | Purpose                                                     |
| ----------------------- | ----------------------------------------------------------- |
| Langfuse · cost/tenant  | Token spend per tenant per day, model breakdown.             |
| Langfuse · trace explorer | Replay any agent run; see the full SDK call tree.          |
| Sentry · errors         | Mobile + backend crash + error rates. Filter by `EXPO_PUBLIC_ENV`. |
| Status page             | Public uptime + incident history.                            |
| Voice latency           | P50/P95/max histograms by region (Mexico City + Cuernavaca). Built off `tests/perf/voice_latency.py` baseline. |
| Comms ring              | `GET /comms/recent?limit=200`. Useful when WhatsApp/SMS delivery looks off. |
| Identity audit          | `GET /_internal/audit` (admin token required). Reads the hash-chained log + verifies the chain integrity. |

Every dashboard URL lives in 1Password (`Casa·Orquesta → Dashboards`). Don't paste them in chat.

---

## 4 · Common incidents (playbooks)

Each playbook starts with the **symptom you'll see** and ends with **how to verify recovery**.

### 4.1 · Voice latency spike (P50 > 1.5 s, P95 > 2.5 s)

**Symptom:** mobile users report long waits between releasing the mic and hearing the reply. The `voice_latency` dashboard shows the P50 line above 1.5 s on the most recent 5-min bucket.

**Triage steps:**
1. `curl -s https://voice.casaorquesta.mx/health | jq .` — confirm gateway up.
2. `curl -s https://voice.casaorquesta.mx/sessions | jq '.count'` — check active session count. If > 200, the gateway is at headroom and may be GC-stalled.
3. Check Anthropic + Deepgram + ElevenLabs status pages.
4. Run `cd services/voice-gateway && python3 tests/perf/voice_latency.py` — confirms the *gateway pipeline* itself isn't slow (vendor latency is separate).
5. If vendor is fine + pipeline benchmark is green → it's probably network. Check `EXPO_PUBLIC_VOICE_GATEWAY_URL` resolution from the Mexico edge.

**Mitigation:** if a single vendor is at fault, flip the fallback env var (e.g. `TTS_PROVIDER=azure` for ElevenLabs outage) and redeploy `voice-gateway` only.

**Verify recovery:** `voice_latency` dashboard P50 back under 1.5 s for 10 minutes straight.

### 4.2 · Identity service returning 5xx

**Symptom:** mobile users can't log in. Sentry alerts on `identity` 500s. `/health` may still 200 (the OTP / Auth0 path is failing, not the process).

**Triage:**
1. `curl -s https://api.casaorquesta.mx/health | jq .` — `mode` should be `real`.
2. Check Auth0 dashboard for tenant-level outage.
3. `curl -s "https://api.casaorquesta.mx/auth/start" -X POST -H 'Content-Type: application/json' -d '{"phone_e164":"+525500000000","locale":"es-MX","invite_code":"OPS-TEST"}'` — replay the start flow.
4. If Auth0 is down: flip `IDENTITY_AUTH0_MODE=stub` and `IDENTITY_EXPOSE_DEV_CODE=0` — degrades to internal-only OTP for ops only, **NOT** for new tester signups (the stub accepts a fixed code; obviously unsuitable for prod). Announce on the status page first.

**Recovery verification:** the synthetic `auth_synthetic_login` Sentry monitor returns green for 3 consecutive runs.

### 4.3 · Comms throttled by WhatsApp Cloud (130429)

**Symptom:** visit confirmations / OTP fallbacks aren't landing. `/comms/recent` shows `reason="rate_limited"` or `error_code=130429`.

**Triage:**
1. Check the Meta Business Manager rate-limit dashboard for the WABA.
2. Inspect `_router.budget._counts` via the comms console for tenant-level cap exhaustion.
3. If a single tenant is over their daily cap, that's expected — they hit a rate-limit:N/M reason. No action needed.
4. If the WABA itself is throttled, comms will already be falling back to Twilio. Confirm Twilio deliveries are landing in `/comms/recent`.

**Mitigation:** if both providers are degraded simultaneously, raise the tenant budget temporarily via `TenantBudget.force_set(tenant_id, 0)` from a one-off Python REPL (requires shell into the comms pod). Document the bump in `#war-room`.

### 4.4 · DSAR fan-out failing

**Symptom:** a user submitted `POST /dsar/export` and the response carries `X-DSAR-Services-Failed > 0`. Check the headers — failed services are listed in the manifest.

**Triage:**
1. `curl -s -H "Authorization: Bearer $USER_JWT" https://api.casaorquesta.mx/dsar/export -o /tmp/dsar.zip`
2. `unzip -p /tmp/dsar.zip manifest.json | jq '.services[] | select(.status != 200)'` — exactly which service(s) failed.
3. For each failing service, hit `GET /dsar/user/{user_id}` directly with the user's JWT. The error response will tell you whether it's auth, network, or a code bug.
4. If a single downstream service is unreachable, the LFPDPPP clock is still ticking (Art. 32: 20 business days). Either restore the service within hours or send the user a follow-up note explaining the partial export.

**Recovery verification:** re-run the export; `X-DSAR-Services-Failed: 0`.

### 4.5 · Audit chain break

**Symptom:** `GET /_internal/audit` returns `chain_ok: false` and the `chain_reason` field names a specific entry id.

**This is a P0.** A break means either (a) database corruption or (b) someone wrote to the table outside the application path. Both block the LFPDPPP audit trail.

**Triage:**
1. Page counsel + founder before touching anything.
2. **Do not** attempt to "fix" the chain — that's tampering.
3. Snapshot the audit table: `pg_dump -t audit_log > /tmp/audit_$(date -u +%Y%m%dT%H%M%S)Z.sql`. Encrypt it (`gpg --symmetric`) and upload to the `casa-incidents` S3 bucket.
4. Bisect: query `SELECT id, prev_hash, content_hash FROM audit_log ORDER BY id` and find the gap.
5. The chain-from-1-to-(N-1) is still valid; entries from N onward are unverifiable. If the break is recent, the LFPDPPP-compliant answer is to issue a public incident note + restore from the most recent verified backup.

### 4.6 · Postgres outage

**Symptom:** every service degrades (most go to in-memory mode and the on-call gets a wave of alerts).

**Triage:**
1. Check the Postgres provider status page.
2. `psql $POSTGRES_URL -c '\l'` to confirm the connection.
3. If Postgres is unreachable AND services have flipped to in-memory mode, **writes during this window are lost on the next restart**. Decide quickly whether to keep accepting writes (risk of data loss) or to set the services read-only via `READONLY=1` env + redeploy.

**Recovery:** see section 6.

### 4.7 · Mobile app boot fails after release

**Symptom:** Sentry alerts on `App.tsx` startup; testers report a black screen.

**Triage:**
1. Confirm `expo doctor` / `eas build --profile development` runs clean from `main`.
2. Check the most recent EAS build's metro logs for missing-module errors. The `apps/mobile/scripts/sanity_check.py` would have caught most of these but a JS-only regression can slip past it.
3. Rollback path: TestFlight has the last good build pinned; switch the "default invite" to it for now. For Play internal track, promote the previous AAB.

---

## 5 · Deploy + rollback

**Deploy** (one service):
```bash
git fetch origin
git checkout main
git pull
./scripts/verify.sh                # must exit 0
fly deploy --app casa-${SVC}        # or your platform's equivalent
fly status --app casa-${SVC}
```

**Rollback:**
```bash
fly releases --app casa-${SVC}                    # find the last good rev
fly deploy --image registry.fly.io/casa-${SVC}:${REV} --app casa-${SVC}
```

**Full-stack rollback** (rare): rollback in dependency order — comms → payments / documents / scheduling / listings → voice-gateway → orchestrator → identity. Reverse for deploys.

Every deploy is mirrored to the deploy log in `#deploys` automatically.

---

## 6 · Database recovery

**Nightly snapshots** live in `s3://casa-backups/postgres/<YYYY-MM-DD>/casa.sql.gz.gpg`. Encrypted with the symmetric key in 1Password (`Casa·Orquesta → Backups`).

**Verify the backup chain (do this monthly):**
```bash
./scripts/backup_postgres.sh --verify-latest
```
The script restores the most recent backup to a throwaway database, runs a smoke query (`SELECT count(*) FROM audit_log` + `verify_chain`), and reports pass/fail.

**Restore in production:**
```bash
# 1. Page counsel + founder.
# 2. Set every service to maintenance mode (the status page first).
# 3. Identify the target snapshot (most recent verified one).
DATE=2026-06-08
aws s3 cp s3://casa-backups/postgres/${DATE}/casa.sql.gz.gpg /tmp/
gpg --decrypt /tmp/casa.sql.gz.gpg | gunzip > /tmp/casa.sql
psql $POSTGRES_URL_RECOVERY < /tmp/casa.sql
# 4. Run the audit-chain integrity check (services/identity/_internal/audit).
# 5. Bring services back online in dependency order.
# 6. Status page incident → resolved.
```

**RPO / RTO commitments** (best-effort, not contractual yet):
- RPO: ≤ 24 hours (nightly backups).
- RTO: ≤ 2 hours for a full restore.

---

## 7 · Secrets + rotation

Documented per-secret in `apps/mobile/SECRETS.md` (mobile EAS secrets) and `docs/LEGAL_PACK.md` (web-side secrets + vendor DPAs).

**Rotation cadence:**
- Auth0 client secret → every 90 days, or immediately on suspected compromise.
- `IDENTITY_JWT_SECRET` → every 90 days. Coordinated rotation across services (deploy with both old + new accepted, then drop old).
- AWS / S3 access keys → every 90 days.
- Sentry DSN → only when changing project.
- Postgres backup encryption key → never automatically; re-key requires re-encrypting historical backups.

**Rotation playbook for `IDENTITY_JWT_SECRET`:**
```
1. Generate new secret: openssl rand -base64 48
2. Add it as `IDENTITY_JWT_SECRET_NEW` in EAS / Fly secrets.
3. Deploy identity with code that accepts both old and new (a P5 task —
   today it only accepts one secret at a time, so you DO take a brief
   logged-out window during rotation).
4. After 1 hour, promote NEW → primary, drop OLD.
5. Confirm `verify_internal_jwt` succeeds against a fresh token.
```

---

## 8 · Escalation tree

```
Page (PagerDuty)
   │
   ├── On-call ack within 5 min ──→ Triage + war-room post
   │
   ├── If user data is at risk:
   │       └── Page counsel + founder *before* triage.
   │
   ├── If LFPDPPP audit chain breaks (4.5):
   │       └── Counsel + founder + freeze writes.
   │
   ├── If unresolved after 30 min:
   │       └── Page secondary on-call.
   │
   └── If unresolved after 60 min OR multi-region outage:
           └── Page founder.
```

**War room hygiene:**
- One person drives. Others observe + assist as asked.
- Post status updates every 15 minutes even if "still investigating".
- When resolved, post a one-line summary + link to the post-mortem.
- Post-mortem template in `docs/post-mortems/_template.md` (create when used).

---

## Appendix · Useful one-liners

```bash
# Identity audit chain integrity (admin JWT required)
curl -s -H "Authorization: Bearer $ADMIN_JWT" \
    https://api.casaorquesta.mx/_internal/audit | jq '{chain_ok, chain_reason, count}'

# Voice-gateway active sessions
curl -s https://voice.casaorquesta.mx/sessions | jq .

# Comms recent (last 20 messages)
curl -s https://api.casaorquesta.mx/comms/recent?limit=20 | jq '.[] | {tenant_id, channel, reason}'

# DSAR fan-out manifest summary
curl -s -o /tmp/dsar.zip -H "Authorization: Bearer $USER_JWT" \
    https://api.casaorquesta.mx/dsar/export
unzip -p /tmp/dsar.zip manifest.json | jq '.summary'

# Verify the full gate locally before deploy
./scripts/verify.sh

# Build the tester guide PDF (locally; needs pandoc + xelatex)
./scripts/build_tester_guide.sh
```
