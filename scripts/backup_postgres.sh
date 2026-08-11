#!/usr/bin/env bash
# backup_postgres — Phase 4.7 nightly DB snapshot to S3/Tigris.
#
# Workflow:
#   1. pg_dump -Fc (custom format) → compressed binary dump.
#   2. gpg --symmetric (AES-256) → encrypted blob.
#   3. aws s3 cp → s3://$S3_BUCKET/postgres/YYYY-MM-DD/casa.dump.gpg
#   4. Prune objects older than 30 days via the S3 lifecycle rule (set
#      once at infra-provision time; documented in docs/RUNBOOK.md §7).
#
# Usage:
#   ./scripts/backup_postgres.sh                       # full backup
#   ./scripts/backup_postgres.sh --dry-run             # no S3 upload
#   ./scripts/backup_postgres.sh --verify-latest       # restore most-recent
#                                                      # snapshot into a
#                                                      # temp DB + smoke check
#
# Required env (production):
#   POSTGRES_URL                 — source database URL
#   S3_BUCKET                    — target bucket (e.g. casa-backups)
#   S3_PREFIX                    — default: postgres
#   AWS_ACCESS_KEY_ID            — caller credentials (or AWS_PROFILE)
#   AWS_SECRET_ACCESS_KEY        — caller credentials
#   AWS_REGION                   — default: us-east-1
#   BACKUP_GPG_RECIPIENT_KEY     — symmetric passphrase (1Password "Backups")
#
# Optional env:
#   AWS_ENDPOINT_URL             — for Tigris / non-AWS S3
#   BACKUP_VERIFY_DB_URL         — separate URL to restore into for --verify-latest
#                                  (defaults to "$POSTGRES_URL?dbname=casa_verify")
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DRY_RUN=0
VERIFY_LATEST=0
S3_PREFIX="${S3_PREFIX:-postgres}"
AWS_REGION="${AWS_REGION:-us-east-1}"

while [ "${1:-}" != "" ]; do
    case "$1" in
        --dry-run)        DRY_RUN=1; shift ;;
        --verify-latest)  VERIFY_LATEST=1; shift ;;
        -h|--help)
            sed -n '2,/^set -euo/p' "$0" | sed -n '2,$p' | head -30
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${YELLOW}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }
need() {
    command -v "$1" >/dev/null 2>&1 || fail "missing dependency: $1"
}

# -----------------------------------------------------------------------------
# Tool checks
# -----------------------------------------------------------------------------
need pg_dump
need gpg
[ "$DRY_RUN" = "1" ] || need aws

# -----------------------------------------------------------------------------
# Verify-latest path
# -----------------------------------------------------------------------------
if [ "$VERIFY_LATEST" = "1" ]; then
    step "Verify most recent backup"
    [ -n "${S3_BUCKET:-}" ] || fail "S3_BUCKET not set"
    [ -n "${BACKUP_GPG_RECIPIENT_KEY:-}" ] || fail "BACKUP_GPG_RECIPIENT_KEY not set"

    LATEST_DATE=$(
        aws s3 ls "s3://${S3_BUCKET}/${S3_PREFIX}/" \
            ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"} \
        | awk '{print $2}' | tr -d '/' | sort | tail -1
    )
    [ -n "$LATEST_DATE" ] || fail "no backups found in s3://${S3_BUCKET}/${S3_PREFIX}/"
    ok "latest snapshot: $LATEST_DATE"

    TMP=$(mktemp -d)
    trap 'rm -rf "$TMP"' EXIT

    SRC="s3://${S3_BUCKET}/${S3_PREFIX}/${LATEST_DATE}/casa.dump.gpg"
    DST="$TMP/casa.dump.gpg"
    aws s3 cp "$SRC" "$DST" \
        ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"}

    echo "$BACKUP_GPG_RECIPIENT_KEY" | gpg --batch --yes --passphrase-fd 0 \
        -o "$TMP/casa.dump" --decrypt "$DST"
    ok "decrypted"

    VERIFY_URL="${BACKUP_VERIFY_DB_URL:-postgres://postgres@localhost/casa_verify}"
    psql "$VERIFY_URL" -c "DROP DATABASE IF EXISTS casa_verify;" >/dev/null || true
    createdb -d "$VERIFY_URL" casa_verify 2>/dev/null || true
    pg_restore --clean --if-exists --no-owner -d "$VERIFY_URL" "$TMP/casa.dump"
    ok "restored to $VERIFY_URL"

    # Smoke checks.
    AUDIT_COUNT=$(psql -tA "$VERIFY_URL" -c "SELECT count(*) FROM audit_log;" 2>/dev/null || echo 0)
    USER_COUNT=$(psql -tA "$VERIFY_URL"  -c "SELECT count(*) FROM users;"     2>/dev/null || echo 0)
    TENANT_COUNT=$(psql -tA "$VERIFY_URL" -c "SELECT count(*) FROM tenants;"  2>/dev/null || echo 0)
    echo "  audit_log rows : $AUDIT_COUNT"
    echo "  users rows     : $USER_COUNT"
    echo "  tenants rows   : $TENANT_COUNT"
    [ "$TENANT_COUNT" -ge 1 ] || fail "no tenants in restored DB"
    ok "smoke check passed"
    exit 0
fi

# -----------------------------------------------------------------------------
# Backup path
# -----------------------------------------------------------------------------
step "Postgres → S3 backup"
[ -n "${POSTGRES_URL:-}" ] || fail "POSTGRES_URL not set"
[ -n "${BACKUP_GPG_RECIPIENT_KEY:-}" ] || fail "BACKUP_GPG_RECIPIENT_KEY not set"
[ "$DRY_RUN" = "1" ] || [ -n "${S3_BUCKET:-}" ] || fail "S3_BUCKET not set"

DATE=$(date -u +%Y-%m-%d)
TS=$(date -u +%Y%m%dT%H%M%SZ)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

DUMP="$TMP/casa.dump"
ENC="$TMP/casa.dump.gpg"

step "pg_dump"
pg_dump --format=custom --compress=9 --no-owner --no-acl \
    --file="$DUMP" "$POSTGRES_URL"
SIZE=$(stat -c%s "$DUMP" 2>/dev/null || stat -f%z "$DUMP")
ok "dumped $(numfmt --to=iec --suffix=B "$SIZE" 2>/dev/null || echo "${SIZE}B")"

step "Encrypt (gpg --symmetric AES-256)"
echo "$BACKUP_GPG_RECIPIENT_KEY" | gpg --batch --yes --passphrase-fd 0 \
    --cipher-algo AES256 --symmetric -o "$ENC" "$DUMP"
ok "encrypted"

if [ "$DRY_RUN" = "1" ]; then
    step "DRY RUN — keeping local copy"
    LOCAL_OUT="${ROOT}/backups/${DATE}/casa.dump.gpg"
    mkdir -p "$(dirname "$LOCAL_OUT")"
    cp "$ENC" "$LOCAL_OUT"
    ok "local copy at $LOCAL_OUT"
    exit 0
fi

step "Upload to s3://${S3_BUCKET}/${S3_PREFIX}/${DATE}/"
S3_KEY="${S3_PREFIX}/${DATE}/casa.dump.gpg"
aws s3 cp "$ENC" "s3://${S3_BUCKET}/${S3_KEY}" \
    ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"} \
    --metadata "backup-ts=${TS},source=casa-orquesta"
ok "uploaded"

# Optional sidecar manifest with the SHA-256 — useful when reading
# from cold storage.
HASH=$(sha256sum "$ENC" | cut -d' ' -f1)
cat > "$TMP/manifest.json" <<EOF
{
  "backup_ts": "${TS}",
  "source": "casa-orquesta",
  "format": "pg_dump --format=custom + gpg AES-256",
  "size_bytes": ${SIZE},
  "sha256_encrypted": "${HASH}"
}
EOF
aws s3 cp "$TMP/manifest.json" \
    "s3://${S3_BUCKET}/${S3_PREFIX}/${DATE}/manifest.json" \
    ${AWS_ENDPOINT_URL:+--endpoint-url "$AWS_ENDPOINT_URL"}
ok "manifest uploaded"

step "Backup complete"
echo "  date         : $DATE"
echo "  s3 path      : s3://${S3_BUCKET}/${S3_KEY}"
echo "  sha256 (enc) : $HASH"
echo "  size         : $(numfmt --to=iec --suffix=B "$SIZE" 2>/dev/null || echo "${SIZE}B")"
echo ""
echo "  Verify with: ./scripts/backup_postgres.sh --verify-latest"
