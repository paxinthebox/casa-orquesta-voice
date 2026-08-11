#!/usr/bin/env bash
# bug_bash_provision — one-shot setup for the Phase 4.5 48-hour bash.
#
# Mints invite codes for the three internal testers (founder + designer +
# advisor), writes them to a CSV, and prints a copy-pasteable WhatsApp
# message template you can send by hand (or pipe to `comms/send/v2` when
# the comms service is up).
#
# Idempotent: the CSV is keyed by tester label; re-running against the
# same CSV file will *only* mint new codes for labels that don't already
# have one.
#
# Usage:
#   ./scripts/bug_bash_provision.sh                       # local generation only
#   ./scripts/bug_bash_provision.sh --identity-url URL --admin-token TOK
#
# Optional flags:
#   --tenant TENANT_ID          (default tnt_pilot_mx)
#   --ttl-days N                (default 14)
#   --out PATH                  (default docs/bug_bash_invites.csv)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TENANT="tnt_pilot_mx"
TTL_DAYS=14
OUT="$ROOT/docs/bug_bash_invites.csv"
IDENTITY_URL=""
ADMIN_TOKEN=""

while [ "${1:-}" != "" ]; do
    case "$1" in
        --tenant)         TENANT="$2"; shift 2 ;;
        --ttl-days)       TTL_DAYS="$2"; shift 2 ;;
        --out)            OUT="$2"; shift 2 ;;
        --identity-url)   IDENTITY_URL="$2"; shift 2 ;;
        --admin-token)    ADMIN_TOKEN="$2"; shift 2 ;;
        -h|--help)
            sed -n '/^# bug_bash_provision/,/^# Optional flags:/p' "$0"
            exit 0 ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
step() { echo -e "\n${YELLOW}━━━ $1 ━━━${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }

step "Bug-bash provisioner"
echo "  tenant          : $TENANT"
echo "  ttl-days        : $TTL_DAYS"
echo "  output csv      : $OUT"
echo "  identity-url    : ${IDENTITY_URL:-<local-only>}"

# All heavy lifting is in this inline Python so we don't need temp files —
# the mounted filesystem doesn't permit deletes, so a "generate-then-merge"
# pattern via .partial files would break. We let Python read the existing
# CSV, mint only the codes we're missing, and rewrite the canonical CSV
# in-place via a simple seek+truncate (write_text covers both creation
# and overwrite).
python3 - "$OUT" "$TENANT" "$TTL_DAYS" "$IDENTITY_URL" "$ADMIN_TOKEN" "$ROOT" <<'PY'
import csv, os, sys
from pathlib import Path
from datetime import datetime, timezone

out_csv     = Path(sys.argv[1])
tenant      = sys.argv[2]
ttl_days    = int(sys.argv[3])
identity    = sys.argv[4] or None
admin_token = sys.argv[5] or None
root        = Path(sys.argv[6])

sys.path.insert(0, str(root / "services" / "identity"))
from store import generate_invite_code, normalize_invite_code  # noqa: E402

TESTERS = ["bash-founder", "bash-designer", "bash-advisor"]

existing = {}
if out_csv.exists():
    with out_csv.open() as f:
        for row in csv.DictReader(f):
            existing[row["label"]] = row

missing = [lbl for lbl in TESTERS if lbl not in existing]
if not missing:
    print(f"  ✓ all 3 testers already have codes in {out_csv}")
else:
    print(f"  → minting {len(missing)} new code(s) for: {', '.join(missing)}")
    ttl_seconds = ttl_days * 86400
    expires_iso = datetime.fromtimestamp(
        datetime.now(tz=timezone.utc).timestamp() + ttl_seconds, tz=timezone.utc
    ).isoformat()

    if identity:
        if not admin_token:
            print("  ✗ --admin-token required with --identity-url", file=sys.stderr)
            sys.exit(2)
        import json, urllib.request
        body = json.dumps({
            "tenant_id": tenant, "count": len(missing),
            "label_prefix": "bash", "role": "buyer",
            "ttl_seconds": ttl_seconds,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{identity.rstrip('/')}/admin/invites/batch",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {admin_token}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            minted = json.loads(resp.read().decode("utf-8")).get("codes", [])
        codes = [c["code"] for c in minted]
    else:
        seen = {existing[lbl]["code"] for lbl in existing}
        codes: list[str] = []
        while len(codes) < len(missing):
            c = generate_invite_code()
            if c in seen or c in codes:
                continue
            codes.append(c)

    for lbl, code in zip(missing, codes):
        existing[lbl] = {
            "code": code, "tenant_id": tenant, "label": lbl,
            "role": "buyer", "expires_at_iso": expires_iso, "notes": "",
        }

# Sanity: every code normalizes to itself.
for row in existing.values():
    if normalize_invite_code(row["code"]) != row["code"]:
        print(f"  ⚠️  code {row['code']!r} does not normalize", file=sys.stderr)

# Write the canonical CSV.
out_csv.parent.mkdir(parents=True, exist_ok=True)
with out_csv.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["code", "tenant_id", "label", "role",
                "expires_at_iso", "notes"])
    for lbl in TESTERS:
        r = existing[lbl]
        w.writerow([r["code"], r["tenant_id"], r["label"], r["role"],
                    r["expires_at_iso"], r.get("notes", "")])

# Echo the message for each tester to stdout — the bash side reads this
# back via the printed lines.
print("---")
for lbl in TESTERS:
    print(f"{lbl}:{existing[lbl]['code']}")
PY

# Parse the tester→code mapping the Python helper just printed.
declare -A CODE_BY=()
while IFS=: read -r lbl code; do
    [ "$lbl" = "---" ] && continue
    case "$lbl" in
        bash-founder|bash-designer|bash-advisor) CODE_BY["$lbl"]="$code" ;;
    esac
done < <(python3 - "$OUT" <<'PY'
import csv, sys
out = sys.argv[1]
with open(out) as f:
    for row in csv.DictReader(f):
        print(f"{row['label']}:{row['code']}")
PY
)

ok "csv ready at $OUT"

# Print a ready-to-paste WhatsApp message template.
step "WhatsApp message (copy/paste to each tester)"

cat <<MSG

Founder (${CODE_BY[bash-founder]:-?}):
  Hola, te invitamos al bug-bash de 48h de Casa·Orquesta. Tu código es:
    ${CODE_BY[bash-founder]:-?}
  Instala desde TestFlight (iOS) o el internal track de Google Play (Android).
  Sigue la guía: docs/tester_guide.md.  Reportes: docs/BUG_BASH.md.
  Caduca en $TTL_DAYS días.

Designer (${CODE_BY[bash-designer]:-?}):
  Same template — código: ${CODE_BY[bash-designer]:-?}

Advisor (${CODE_BY[bash-advisor]:-?}):
  Same template — código: ${CODE_BY[bash-advisor]:-?}

MSG

step "Next step"
echo "  Each tester fills in docs/DEVICE_QA.md (P3.4) + reports issues in"
echo "  docs/BUG_BASH.md (P4.5). Validate before ship with:"
echo "    python3 scripts/bug_bash_report.py"
